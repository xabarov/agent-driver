"""opencode-adoption EPIC-06 — real stdio MCP client + registrar.

Exercised end-to-end against a self-contained fake MCP server (a tiny Python program
that speaks newline-delimited JSON-RPC 2.0 on stdio), so the client is verified for real
— handshake, tools/list, tools/call, error mapping, discovery/registration into a
ToolRegistry with namespaced names, allowlist filtering, and clean shutdown. No third-party
MCP SDK or network is involved.
"""

from __future__ import annotations

import sys

import pytest

from agent_driver.tools import ToolRegistry
from agent_driver.tools.mcp_client import (
    McpProtocolError,
    StdioMcpClient,
    StdioServerConfig,
    namespaced_tool_name,
    register_stdio_mcp_server,
)

# A minimal but spec-shaped MCP stdio server: reads one JSON-RPC message per line,
# writes one JSON response per line. Supports initialize, tools/list (paginated in two
# pages), tools/call (echo + boom + unknown->error), and ignores notifications.
_FAKE_SERVER = r'''
import json, sys

def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()

TOOLS_PAGE_1 = [{
    "name": "echo",
    "description": "Echo the arguments back as text.",
    "inputSchema": {"type": "object", "properties": {"value": {"type": "string"}}},
}]
TOOLS_PAGE_2 = [{
    "name": "boom",
    "description": "Always returns a tool-level error.",
    "inputSchema": {"type": "object"},
}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    msg = json.loads(line)
    method = msg.get("method")
    mid = msg.get("id")
    if method == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "fake", "version": "1.2.3"},
            "capabilities": {"tools": {}},
        }})
    elif method == "notifications/initialized":
        pass  # notification, no reply
    elif method == "tools/list":
        cursor = (msg.get("params") or {}).get("cursor")
        if cursor is None:
            send({"jsonrpc": "2.0", "id": mid,
                  "result": {"tools": TOOLS_PAGE_1, "nextCursor": "page2"}})
        else:
            send({"jsonrpc": "2.0", "id": mid, "result": {"tools": TOOLS_PAGE_2}})
    elif method == "tools/call":
        params = msg.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": json.dumps(args)}],
                "isError": False,
            }})
        elif name == "boom":
            send({"jsonrpc": "2.0", "id": mid, "result": {
                "content": [{"type": "text", "text": "kaboom"}],
                "isError": True,
            }})
        else:
            send({"jsonrpc": "2.0", "id": mid,
                  "error": {"code": -32601, "message": f"unknown tool {name}"}})
    else:
        send({"jsonrpc": "2.0", "id": mid,
              "error": {"code": -32601, "message": f"unknown method {method}"}})
'''


def _config(tmp_path, **overrides) -> StdioServerConfig:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return StdioServerConfig(
        server_id="demo",
        command=sys.executable,
        args=(str(script),),
        **overrides,
    )


@pytest.mark.asyncio
async def test_client_handshake_and_list_tools(tmp_path) -> None:
    async with StdioMcpClient(_config(tmp_path)) as client:
        assert client.server_info.get("name") == "fake"
        tools = await client.list_tools()
        # Two pages were transparently concatenated by cursor walking.
        names = {t["name"] for t in tools}
        assert names == {"echo", "boom"}


@pytest.mark.asyncio
async def test_client_call_tool_success_and_tool_error(tmp_path) -> None:
    async with StdioMcpClient(_config(tmp_path)) as client:
        ok = await client.call_tool("echo", {"value": "hi"})
        assert ok["isError"] is False
        assert '"value": "hi"' in ok["content"][0]["text"]

        boom = await client.call_tool("boom", {})
        assert boom["isError"] is True


@pytest.mark.asyncio
async def test_client_protocol_error_on_unknown_tool(tmp_path) -> None:
    async with StdioMcpClient(_config(tmp_path)) as client:
        with pytest.raises(McpProtocolError) as excinfo:
            await client.call_tool("nope", {})
        assert excinfo.value.code == -32601


@pytest.mark.asyncio
async def test_register_discovers_and_namespaces_tools(tmp_path) -> None:
    registry = ToolRegistry()
    registration = await register_stdio_mcp_server(registry, _config(tmp_path))
    try:
        assert registration.server_info.get("version") == "1.2.3"
        assert set(registration.tool_names) == {
            namespaced_tool_name("demo", "echo"),
            namespaced_tool_name("demo", "boom"),
        }
        # The registered tool is invocable and proxies to tools/call.
        registered = registry.get(namespaced_tool_name("demo", "echo"))
        assert registered is not None
        out = await registered.handler({"value": "x"})
        assert out["is_error"] is False
        assert '"value": "x"' in out["text"]
        # Discovered manifest carries external-server governance provenance.
        prov = registered.manifest.metadata["descriptor_provenance"]
        assert prov["inventory_source"] == "mcp_stdio"
        assert prov["server_id"] == "demo"
    finally:
        await registration.client.aclose()


@pytest.mark.asyncio
async def test_register_honors_tool_allowlist(tmp_path) -> None:
    registry = ToolRegistry()
    config = _config(tmp_path, tool_allowlist=frozenset({"echo"}))
    registration = await register_stdio_mcp_server(registry, config)
    try:
        assert registration.tool_names == (namespaced_tool_name("demo", "echo"),)
        assert registry.get(namespaced_tool_name("demo", "boom")) is None
    finally:
        await registration.client.aclose()


@pytest.mark.asyncio
async def test_bad_command_raises_transport_error(tmp_path) -> None:
    from agent_driver.tools.mcp_client import McpTransportError

    config = StdioServerConfig(
        server_id="broken",
        command="/nonexistent/mcp-server-binary-xyz",
    )
    with pytest.raises(McpTransportError):
        await register_stdio_mcp_server(ToolRegistry(), config)
