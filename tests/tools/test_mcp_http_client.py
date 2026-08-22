"""opencode-adoption EPIC-06 (HTTP follow-on) — streamable-HTTP MCP client.

Driven offline by an ``httpx.MockTransport`` that speaks the streamable-HTTP protocol
(SSE-framed initialize with an ``Mcp-Session-Id`` header, JSON tools/list + tools/call,
202 for notifications), so the client's session handling, SSE parsing, id matching, and
error mapping are verified without a live server. A separate live test exercises the real
reference server.
"""

from __future__ import annotations

import json

import httpx
import pytest

from agent_driver.tools import ToolRegistry
from agent_driver.tools.mcp_client import (
    HttpMcpClient,
    HttpServerConfig,
    McpProtocolError,
    namespaced_tool_name,
    register_mcp_client,
)
from agent_driver.tools.mcp_client.http_client import _parse_sse_messages

_SESSION_ID = "sess-123"


def _sse(payload: dict) -> httpx.Response:
    body = f"event: message\ndata: {json.dumps(payload)}\n\n"
    return httpx.Response(
        200, headers={"content-type": "text/event-stream"}, text=body
    )


def _json(payload: dict) -> httpx.Response:
    return httpx.Response(200, json=payload)


def _handler(request: httpx.Request) -> httpx.Response:
    msg = json.loads(request.content)
    method = msg.get("method")
    rid = msg.get("id")
    if method == "initialize":
        # session id returned in a header; body is SSE-framed
        resp = _sse(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "protocolVersion": "2025-06-18",
                    "serverInfo": {"name": "mock", "version": "9.9"},
                    "capabilities": {"tools": {}},
                },
            }
        )
        resp.headers["mcp-session-id"] = _SESSION_ID
        return resp
    if method == "notifications/initialized":
        return httpx.Response(202)
    # every post-init request must echo the session header
    assert request.headers.get("mcp-session-id") == _SESSION_ID
    assert request.headers.get("mcp-protocol-version") == "2025-06-18"
    if method == "tools/list":
        return _json(
            {
                "jsonrpc": "2.0",
                "id": rid,
                "result": {
                    "tools": [
                        {"name": "echo", "description": "e", "inputSchema": {}},
                        {"name": "get-sum", "description": "s", "inputSchema": {}},
                    ]
                },
            }
        )
    if method == "tools/call":
        name = (msg.get("params") or {}).get("name")
        args = (msg.get("params") or {}).get("arguments") or {}
        if name == "get-sum":
            total = (args.get("a") or 0) + (args.get("b") or 0)
            return _sse(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "result": {"content": [{"type": "text", "text": str(total)}]},
                }
            )
        if name == "nope":
            return _json(
                {
                    "jsonrpc": "2.0",
                    "id": rid,
                    "error": {"code": -32601, "message": "unknown tool"},
                }
            )
    return _json({"jsonrpc": "2.0", "id": rid, "error": {"code": -1, "message": "?"}})


def _client() -> HttpMcpClient:
    transport = httpx.MockTransport(_handler)
    return HttpMcpClient(
        HttpServerConfig(server_id="mock", url="https://mock.invalid/mcp"),
        httpx_client=httpx.AsyncClient(transport=transport),
    )


def test_parse_sse_messages() -> None:
    body = (
        "event: message\nid: a\ndata: {\"id\":1,\"result\":{\"ok\":true}}\n\n"
        "event: message\ndata: {\"id\":2}\n\n"
    )
    msgs = _parse_sse_messages(body)
    assert msgs[0]["result"]["ok"] is True
    assert msgs[1]["id"] == 2
    assert _parse_sse_messages("garbage no data lines") == []


@pytest.mark.asyncio
async def test_http_handshake_captures_session_and_lists_tools() -> None:
    async with _client() as client:
        assert client.server_info["name"] == "mock"
        assert client._session_id == _SESSION_ID
        tools = await client.list_tools()
        assert {t["name"] for t in tools} == {"echo", "get-sum"}


@pytest.mark.asyncio
async def test_http_call_tool_via_sse_and_error_mapping() -> None:
    async with _client() as client:
        ok = await client.call_tool("get-sum", {"a": 40, "b": 2})
        assert ok["content"][0]["text"] == "42"
        with pytest.raises(McpProtocolError) as exc:
            await client.call_tool("nope", {})
        assert exc.value.code == -32601


@pytest.mark.asyncio
async def test_http_registers_governed_namespaced_tools() -> None:
    registry = ToolRegistry()
    reg = await register_mcp_client(
        registry, _client(), server_id="mock", tool_allowlist=None
    )
    try:
        assert set(reg.tool_names) == {
            namespaced_tool_name("mock", "echo"),
            namespaced_tool_name("mock", "get-sum"),
        }
        entry = registry.get(namespaced_tool_name("mock", "get-sum"))
        out = await entry.handler({"a": 7, "b": 5})
        assert out["is_error"] is False
        assert out["text"] == "12"
    finally:
        await reg.client.aclose()


@pytest.mark.asyncio
async def test_http_transport_error_on_500() -> None:
    from agent_driver.tools.mcp_client import McpTransportError

    def boom(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="upstream down")

    client = HttpMcpClient(
        HttpServerConfig(server_id="mock", url="https://mock.invalid/mcp"),
        httpx_client=httpx.AsyncClient(transport=httpx.MockTransport(boom)),
    )
    with pytest.raises(McpTransportError):
        await client.start()
    await client.aclose()
