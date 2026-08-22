"""opencode-adoption EPIC-06 — OAuth 2.0 + PKCE helpers for the HTTP MCP transport.

The interactive consent (browser + redirect) is host-driven; the testable core — PKCE
generation, the authorization URL, and the token-endpoint round-trips — is pinned here,
with the token POSTs driven offline via ``httpx.MockTransport``.
"""

from __future__ import annotations

import base64
import hashlib
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from agent_driver.tools.mcp_client import (
    HttpServerConfig,
    bearer_headers,
    build_authorization_url,
    exchange_code_for_token,
    generate_pkce_pair,
    refresh_access_token,
)


def test_pkce_pair_is_valid_s256() -> None:
    a, b = generate_pkce_pair(), generate_pkce_pair()
    assert a.verifier != b.verifier  # fresh each call
    assert a.method == "S256"
    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(a.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert a.challenge == expected
    assert 43 <= len(a.verifier) <= 128


def test_build_authorization_url() -> None:
    url = build_authorization_url(
        authorization_endpoint="https://idp.example/authorize",
        client_id="cid",
        redirect_uri="http://localhost:9999/cb",
        code_challenge="chal",
        scopes=("mcp.read", "mcp.call"),
        state="xyz",
    )
    q = parse_qs(urlparse(url).query)
    assert q["response_type"] == ["code"]
    assert q["client_id"] == ["cid"]
    assert q["code_challenge"] == ["chal"]
    assert q["code_challenge_method"] == ["S256"]
    assert q["scope"] == ["mcp.read mcp.call"]
    assert q["state"] == ["xyz"]


def test_bearer_headers_merge_into_http_config() -> None:
    headers = bearer_headers("tok-123")
    assert headers == {"Authorization": "Bearer tok-123"}
    cfg = HttpServerConfig(server_id="s", url="https://h/mcp", headers=headers)
    assert cfg.headers["Authorization"] == "Bearer tok-123"
    with pytest.raises(ValueError):
        bearer_headers("")


def _token_client(expected_grant: str) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        form = parse_qs(request.content.decode())
        assert form["grant_type"] == [expected_grant]
        assert form["client_id"] == ["cid"]
        if expected_grant == "authorization_code":
            assert form["code"] == ["the-code"]
            assert form["code_verifier"] == ["the-verifier"]
        else:
            assert form["refresh_token"] == ["rt-1"]
        return httpx.Response(
            200,
            json={
                "access_token": "at-1",
                "token_type": "Bearer",
                "refresh_token": "rt-2",
                "expires_in": 3600,
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_exchange_code_for_token() -> None:
    async with _token_client("authorization_code") as client:
        tok = await exchange_code_for_token(
            token_endpoint="https://idp.example/token",
            client_id="cid",
            code="the-code",
            code_verifier="the-verifier",
            redirect_uri="http://localhost:9999/cb",
            httpx_client=client,
        )
    assert tok["access_token"] == "at-1"
    assert bearer_headers(tok["access_token"]) == {"Authorization": "Bearer at-1"}


@pytest.mark.asyncio
async def test_refresh_access_token() -> None:
    async with _token_client("refresh_token") as client:
        tok = await refresh_access_token(
            token_endpoint="https://idp.example/token",
            client_id="cid",
            refresh_token="rt-1",
            httpx_client=client,
        )
    assert tok["access_token"] == "at-1"


@pytest.mark.asyncio
async def test_token_endpoint_without_access_token_raises() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"error": "invalid_grant"})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(ValueError):
        await refresh_access_token(
            token_endpoint="https://idp.example/token",
            client_id="cid",
            refresh_token="rt-1",
            httpx_client=client,
        )
    await client.aclose()
