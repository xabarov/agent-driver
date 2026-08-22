"""opencode-adoption EPIC-06 — SDK MCP server-list wiring.

``connect_mcp_servers`` connects a host-declared list of MCP servers into a built agent's
live tool registry (dispatching stdio vs HTTP); ``close_mcp_servers`` shuts them down
best-effort. Exercised against a self-contained fake stdio server.
"""

from __future__ import annotations

import sys

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import (
    close_mcp_servers,
    connect_mcp_servers,
    create_agent,
)
from agent_driver.tools.mcp_client import StdioServerConfig, namespaced_tool_name

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
            {"name": "ping", "description": "p", "inputSchema": {}}]}})
    elif meth == "tools/call":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "pong"}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -1, "message": "?"}})
'''


def _stdio_config(tmp_path, server_id="fk") -> StdioServerConfig:
    script = tmp_path / f"{server_id}_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return StdioServerConfig(
        server_id=server_id, command=sys.executable, args=(str(script),)
    )


@pytest.mark.asyncio
async def test_connect_registers_tools_into_agent(tmp_path) -> None:
    agent = create_agent(provider=FakeProvider())
    regs = await connect_mcp_servers(agent, [_stdio_config(tmp_path)])
    try:
        assert len(regs) == 1
        name = namespaced_tool_name("fk", "ping")
        assert name in regs[0].tool_names
        entry = agent._runner.deps.tool_registry.get(name)
        assert entry is not None  # visible in the agent's live registry
        out = await entry.handler({})
        assert out["text"] == "pong"
    finally:
        await close_mcp_servers(regs)


@pytest.mark.asyncio
async def test_connect_multiple_servers_namespaced(tmp_path) -> None:
    agent = create_agent(provider=FakeProvider())
    regs = await connect_mcp_servers(
        agent,
        [_stdio_config(tmp_path, "srvA"), _stdio_config(tmp_path, "srvB")],
    )
    try:
        registry = agent._runner.deps.tool_registry
        assert registry.get(namespaced_tool_name("srvA", "ping")) is not None
        assert registry.get(namespaced_tool_name("srvB", "ping")) is not None
    finally:
        await close_mcp_servers(regs)


@pytest.mark.asyncio
async def test_connect_rejects_unknown_config() -> None:
    agent = create_agent(provider=FakeProvider())
    with pytest.raises(TypeError):
        await connect_mcp_servers(agent, [object()])  # type: ignore[list-item]


@pytest.mark.asyncio
async def test_close_is_best_effort() -> None:
    class _Boom:
        async def aclose(self):
            raise RuntimeError("close failed")

    class _Reg:
        client = _Boom()

    # a raising client must not stop the others from closing
    await close_mcp_servers([_Reg(), _Reg()])  # no exception
