"""Tests for the agent-driver MCP server surface."""

from __future__ import annotations

import json

import pytest

from agent_driver.llm import FakeProvider
from agent_driver.mcp_server import (
    MCP_PROTOCOL_VERSION,
    AgentMcpServer,
    serve_stream,
)
from agent_driver.sdk import ToolSet, create_agent


def _server(answer: str = "hello from agent") -> AgentMcpServer:
    agent = create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )
    return AgentMcpServer(agent)


def _req(method: str, params: dict | None = None, request_id: int | None = 1) -> dict:
    req: dict = {"jsonrpc": "2.0", "method": method}
    if request_id is not None:
        req["id"] = request_id
    if params is not None:
        req["params"] = params
    return req


@pytest.mark.asyncio
async def test_initialize() -> None:
    resp = await _server().handle_request(_req("initialize"))
    assert resp["result"]["protocolVersion"] == MCP_PROTOCOL_VERSION
    assert resp["result"]["serverInfo"]["name"] == "agent-driver"
    assert "tools" in resp["result"]["capabilities"]


@pytest.mark.asyncio
async def test_tools_list_advertises_tools() -> None:
    resp = await _server().handle_request(_req("tools/list"))
    names = {t["name"] for t in resp["result"]["tools"]}
    assert names == {"agent_query", "session_send", "session_history"}
    for tool in resp["result"]["tools"]:
        assert tool["inputSchema"]["type"] == "object"


@pytest.mark.asyncio
async def test_tools_call_agent_query() -> None:
    resp = await _server("answer-42").handle_request(
        _req("tools/call", {"name": "agent_query", "arguments": {"input": "hi"}})
    )
    result = resp["result"]
    assert result["isError"] is False
    assert result["content"][0]["text"] == "answer-42"
    assert result["structuredContent"]["answer"] == "answer-42"
    assert result["structuredContent"]["status"] == "completed"


@pytest.mark.asyncio
async def test_session_send_then_history() -> None:
    server = _server("ok")
    await server.handle_request(
        _req(
            "tools/call",
            {
                "name": "session_send",
                "arguments": {"session_id": "s1", "input": "hello"},
            },
        )
    )
    resp = await server.handle_request(
        _req(
            "tools/call",
            {"name": "session_history", "arguments": {"session_id": "s1"}},
        )
    )
    turns = resp["result"]["structuredContent"]["turns"]
    assert turns, "expected at least one persisted turn"
    # Roles serialize as plain strings (JSON-safe), not enums.
    assert all(isinstance(t["role"], str) for t in turns)
    assert any(t["role"] == "assistant" and t["content"] == "ok" for t in turns)
    # The history round-trips through json (structuredContent must be JSON-safe).
    json.dumps(resp["result"])


@pytest.mark.asyncio
async def test_unknown_tool_is_in_band_error() -> None:
    resp = await _server().handle_request(
        _req("tools/call", {"name": "nope", "arguments": {}})
    )
    assert resp["result"]["isError"] is True
    assert "unknown tool" in resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_missing_required_arg_is_in_band_error() -> None:
    resp = await _server().handle_request(
        _req("tools/call", {"name": "agent_query", "arguments": {}})
    )
    assert resp["result"]["isError"] is True
    assert "input is required" in resp["result"]["content"][0]["text"]


@pytest.mark.asyncio
async def test_unknown_method_is_protocol_error() -> None:
    resp = await _server().handle_request(_req("does/not/exist"))
    assert resp["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_notification_returns_no_response() -> None:
    resp = await _server().handle_request(
        _req("notifications/initialized", request_id=None)
    )
    assert resp is None


@pytest.mark.asyncio
async def test_serve_stream_pumps_jsonrpc_lines() -> None:
    server = _server("streamed")
    written: list[str] = []

    async def _lines():
        yield json.dumps(_req("initialize"))
        yield ""  # blank line skipped
        yield "{ not json"  # parse error
        yield json.dumps(
            _req("tools/call", {"name": "agent_query", "arguments": {"input": "x"}}, 2)
        )
        yield json.dumps(_req("notifications/initialized", request_id=None))

    await serve_stream(server, _lines(), written.append)

    responses = [json.loads(line) for line in written]
    # initialize ok, parse error, tools/call ok — notification produced nothing.
    assert len(responses) == 3
    assert responses[0]["id"] == 1
    assert responses[1]["error"]["code"] == -32700
    assert responses[2]["result"]["content"][0]["text"] == "streamed"
