"""opencode-adoption EPIC-06 — tools/list_changed live refresh (stdio).

The client surfaces ``notifications/tools/list_changed`` via ``tools_changed_event``, and
``resync_mcp_server_tools`` re-discovers + reconciles the registry (adds new, updates
existing, unregisters dropped tools). Driven by a fake server that changes its tool set on
the second ``tools/list`` and emits the change notification.
"""

from __future__ import annotations

import sys

import pytest

from agent_driver.tools import ToolRegistry
from agent_driver.tools.mcp_client import (
    StdioServerConfig,
    namespaced_tool_name,
    register_stdio_mcp_server,
    resync_mcp_server_tools,
)

# First tools/list -> {alpha, beta}; after a signal it emits tools/list_changed and the
# next tools/list -> {alpha, gamma} (beta dropped, gamma added).
_FAKE_SERVER = r'''
import json, sys

def send(o):
    sys.stdout.write(json.dumps(o) + "\n"); sys.stdout.flush()

calls = {"list": 0}
PAGE_A = [{"name": "alpha", "inputSchema": {}}, {"name": "beta", "inputSchema": {}}]
PAGE_B = [{"name": "alpha", "inputSchema": {}}, {"name": "gamma", "inputSchema": {}}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    m = json.loads(line); meth = m.get("method"); mid = m.get("id")
    if meth == "initialize":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "protocolVersion": "2025-06-18",
            "serverInfo": {"name": "fk", "version": "1"},
            "capabilities": {"tools": {"listChanged": True}}}})
    elif meth == "notifications/initialized":
        pass
    elif meth == "tools/list":
        calls["list"] += 1
        page = PAGE_A if calls["list"] == 1 else PAGE_B
        send({"jsonrpc": "2.0", "id": mid, "result": {"tools": page}})
        if calls["list"] == 1:
            # announce the change so the client's event fires
            send({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
    elif meth == "tools/call":
        send({"jsonrpc": "2.0", "id": mid, "result": {
            "content": [{"type": "text", "text": "ok"}], "isError": False}})
    else:
        send({"jsonrpc": "2.0", "id": mid, "error": {"code": -1, "message": "?"}})
'''


def _config(tmp_path) -> StdioServerConfig:
    script = tmp_path / "changing_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return StdioServerConfig(
        server_id="fk", command=sys.executable, args=(str(script),)
    )


@pytest.mark.asyncio
async def test_list_changed_event_and_resync(tmp_path) -> None:
    registry = ToolRegistry()
    reg = await register_stdio_mcp_server(registry, _config(tmp_path))
    try:
        alpha = namespaced_tool_name("fk", "alpha")
        beta = namespaced_tool_name("fk", "beta")
        gamma = namespaced_tool_name("fk", "gamma")
        # initial discovery
        assert set(reg.tool_names) == {alpha, beta}

        # the server announced a change; the client's event is set
        await reg.client.tools_changed_event.wait()

        # resync reconciles: gamma added, beta (dropped by the server) unregistered
        reg2 = await resync_mcp_server_tools(registry, reg)
        assert set(reg2.tool_names) == {alpha, gamma}
        assert registry.get(alpha) is not None
        assert registry.get(gamma) is not None
        assert registry.get(beta) is None  # retired
        # the change event was cleared by resync
        assert reg.client.tools_changed_event.is_set() is False
    finally:
        await reg.client.aclose()


def test_registry_unregister() -> None:
    from agent_driver.contracts import ToolManifest

    async def _h(_a):
        return {}

    reg = ToolRegistry()
    reg.register(ToolManifest(name="t1", description="d"), _h)
    assert reg.get("t1") is not None
    assert reg.unregister("t1") is True
    assert reg.get("t1") is None
    assert reg.unregister("t1") is False  # already gone
