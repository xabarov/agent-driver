"""opencode-adoption EPIC-06 — ACP mcp_servers -> outward MCP client wiring.

Translates ACP ``McpServerStdio`` / ``McpServerHttp`` session descriptors into runtime MCP
configs, and connects the declared servers (deduped by server_id) into the ACP adapter's
shared agent registry via ``connect_acp_mcp_servers``.
"""

from __future__ import annotations

import sys

import acp.schema as s
import pytest

from agent_driver.adapters.acp.mcp import acp_mcp_configs, connect_acp_mcp_servers
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools.mcp_client import (
    HttpServerConfig,
    StdioServerConfig,
    namespaced_tool_name,
)

_FAKE_SERVER = r'''
import json, sys
def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()
for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    m = json.loads(line); meth = m.get("method"); mid = m.get("id")
    if meth == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "fk", "version": "1"}, "capabilities": {}}})
    elif meth == "notifications/initialized":
        pass
    elif meth == "tools/list":
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
            {"name": "ping", "inputSchema": {}}]}})
    elif meth == "tools/call":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "pong"}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -1, "message": "?"}})
'''


def test_acp_translate_stdio_and_http() -> None:
    servers = [
        s.McpServerStdio(
            name="fs",
            command="mcp-fs",
            args=["/data"],
            env=[s.EnvVariable(name="K", value="V")],
        ),
        s.McpServerHttp(
            name="docs",
            url="https://h/mcp",
            headers=[s.HttpHeader(name="Authorization", value="Bearer x")],
        ),
    ]
    cfgs = acp_mcp_configs(servers)
    assert isinstance(cfgs[0], StdioServerConfig)
    assert cfgs[0].server_id == "fs" and cfgs[0].args == ("/data",)
    assert cfgs[0].env == {"K": "V"}
    assert isinstance(cfgs[1], HttpServerConfig)
    assert cfgs[1].headers == {"Authorization": "Bearer x"}


def test_acp_translate_skips_unnamed_and_unknown() -> None:
    # an SSE descriptor (no command/url that we support) and an unnamed one are skipped
    assert acp_mcp_configs([s.McpServerStdio(name="", command="x", args=[], env=[])]) == []


@pytest.mark.asyncio
async def test_connect_acp_mcp_servers_dedupes(tmp_path) -> None:
    script = tmp_path / "srv.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    agent = create_agent(provider=FakeProvider())
    declared = [s.McpServerStdio(name="fk", command=sys.executable, args=[str(script)], env=[])]
    connected: dict = {}

    first = await connect_acp_mcp_servers(agent, declared, already_connected=connected)
    try:
        assert len(first) == 1
        name = namespaced_tool_name("fk", "ping")
        assert agent._runner.deps.tool_registry.get(name) is not None
        out = await agent._runner.deps.tool_registry.get(name).handler({})
        assert out["text"] == "pong"

        # a second session declaring the same server_id connects nothing new
        second = await connect_acp_mcp_servers(
            agent, declared, already_connected=connected
        )
        assert second == []
        assert set(connected) == {"fk"}
    finally:
        for reg in connected.values():
            await reg.client.aclose()


@pytest.mark.asyncio
async def test_connect_is_best_effort_on_bad_server(tmp_path) -> None:
    agent = create_agent(provider=FakeProvider())
    bad = [s.McpServerStdio(name="broken", command="/no/such/binary-xyz", args=[], env=[])]
    connected: dict = {}
    # a server that fails to launch is skipped, not raised
    result = await connect_acp_mcp_servers(agent, bad, already_connected=connected)
    assert result == []
    assert connected == {}
