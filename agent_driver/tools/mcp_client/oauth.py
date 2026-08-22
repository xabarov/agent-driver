"""OAuth 2.0 + PKCE helpers for authenticating to a remote MCP server (EPIC-06).

The MCP HTTP transport authenticates with a bearer token in the ``Authorization`` header
(``HttpServerConfig.headers``). These helpers implement the **testable, non-interactive**
core of the OAuth authorization-code + PKCE flow:

* :func:`generate_pkce_pair` — a fresh ``code_verifier`` / ``code_challenge`` (S256);
* :func:`build_authorization_url` — the URL the host opens in the user's browser;
* :func:`exchange_code_for_token` / :func:`refresh_access_token` — the token-endpoint
  round-trips (HTTP POST), returning the token response;
* :func:`bearer_headers` — turn an access token into the header dict to merge into
  ``HttpServerConfig.headers``.

The **interactive** step — the user opening the authorization URL and the redirect
delivering the ``code`` — is inherently host-driven (a browser + a redirect listener) and
is not part of a headless library. The host calls :func:`build_authorization_url`, obtains
the ``code`` however it can, then calls :func:`exchange_code_for_token`.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


@dataclass(frozen=True, slots=True)
class PkcePair:
    """A PKCE ``code_verifier`` and its S256 ``code_challenge``."""

    verifier: str
    challenge: str
    method: str = "S256"


def generate_pkce_pair() -> PkcePair:
    """Return a fresh PKCE pair (RFC 7636 S256): a random verifier + its SHA-256
    challenge. The verifier stays with the host until token exchange; only the challenge
    goes in the authorization URL."""
    verifier = _b64url(secrets.token_bytes(32))  # 43 chars, within 43–128
    challenge = _b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    return PkcePair(verifier=verifier, challenge=challenge)


def build_authorization_url(
    *,
    authorization_endpoint: str,
    client_id: str,
    redirect_uri: str,
    code_challenge: str,
    scopes: tuple[str, ...] = (),
    state: str | None = None,
    extra_params: dict[str, str] | None = None,
) -> str:
    """Build the authorization-code + PKCE URL for the host to open in a browser."""
    params: dict[str, str] = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_challenge": code_challenge,
        "code_challenge_method": "S256",
    }
    if scopes:
        params["scope"] = " ".join(scopes)
    if state:
        params["state"] = state
    if extra_params:
        params.update(extra_params)
    sep = "&" if "?" in authorization_endpoint else "?"
    return f"{authorization_endpoint}{sep}{urlencode(params)}"


async def _post_token(
    token_endpoint: str, form: dict[str, str], *, httpx_client: Any = None
) -> dict[str, Any]:
    import httpx  # noqa: PLC0415

    client = httpx_client or httpx.AsyncClient()
    owns_client = httpx_client is None
    try:
        response = await client.post(
            token_endpoint,
            data=form,
            headers={"Accept": "application/json"},
        )
        response.raise_for_status()
        payload = response.json()
    finally:
        if owns_client:
            await client.aclose()
    if not isinstance(payload, dict) or "access_token" not in payload:
        raise ValueError("token endpoint did not return an access_token")
    return payload


async def exchange_code_for_token(
    *,
    token_endpoint: str,
    client_id: str,
    code: str,
    code_verifier: str,
    redirect_uri: str,
    client_secret: str | None = None,
    httpx_client: Any = None,
) -> dict[str, Any]:
    """Exchange an authorization ``code`` (+ the PKCE ``code_verifier``) for tokens."""
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": code_verifier,
    }
    if client_secret:
        form["client_secret"] = client_secret
    return await _post_token(token_endpoint, form, httpx_client=httpx_client)


async def refresh_access_token(
    *,
    token_endpoint: str,
    client_id: str,
    refresh_token: str,
    client_secret: str | None = None,
    httpx_client: Any = None,
) -> dict[str, Any]:
    """Exchange a ``refresh_token`` for a fresh access token."""
    form = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret:
        form["client_secret"] = client_secret
    return await _post_token(token_endpoint, form, httpx_client=httpx_client)


def bearer_headers(access_token: str) -> dict[str, str]:
    """``{"Authorization": "Bearer <token>"}`` — merge into ``HttpServerConfig.headers``."""
    if not access_token:
        raise ValueError("access_token is required")
    return {"Authorization": f"Bearer {access_token}"}


__all__ = [
    "PkcePair",
    "bearer_headers",
    "build_authorization_url",
    "exchange_code_for_token",
    "generate_pkce_pair",
    "refresh_access_token",
]
