"""Live interop: our StdioMcpClient vs the official reference MCP server.

Marked ``live`` (excluded from the default sweep) and ``slow`` — it launches
``npx -y @modelcontextprotocol/server-everything stdio``, which downloads the package on
first run and needs network + node/npx. Run explicitly with::

    .venv/bin/python -m pytest tests/tools/test_mcp_client_live.py -m live

This is the regression that first surfaced the kebab-case tool-name bug (the reference
server's tools are named ``get-sum``, ``get-tiny-image``, …).
"""

from __future__ import annotations

import shutil

import pytest

from agent_driver.tools import ToolRegistry
from agent_driver.tools.mcp_client import (
    StdioMcpClient,
    StdioServerConfig,
    namespaced_tool_name,
    register_stdio_mcp_server,
)

pytestmark = [pytest.mark.live, pytest.mark.slow]


def _config() -> StdioServerConfig:
    return StdioServerConfig(
        server_id="everything",
        command="npx",
        args=("-y", "@modelcontextprotocol/server-everything", "stdio"),
        init_timeout_seconds=90.0,
        request_timeout_seconds=30.0,
    )


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx (node) is not available")
@pytest.mark.asyncio
async def test_live_reference_server_end_to_end() -> None:
    async with StdioMcpClient(_config()) as client:
        assert "everything" in str(client.server_info.get("name", "")).lower()
        tools = await client.list_tools()
        names = {t.get("name") for t in tools}
        # The reference server exposes kebab-case tools.
        assert "echo" in names
        assert "get-sum" in names

        echoed = await client.call_tool("echo", {"message": "hi"})
        text = "".join(
            b.get("text", "") for b in echoed["content"] if b.get("type") == "text"
        )
        assert "hi" in text

        summed = await client.call_tool("get-sum", {"a": 40, "b": 2})
        assert summed.get("isError") in (False, None)
        assert "42" in "".join(
            b.get("text", "") for b in summed["content"] if b.get("type") == "text"
        )


@pytest.mark.skipif(shutil.which("npx") is None, reason="npx (node) is not available")
@pytest.mark.asyncio
async def test_live_reference_server_registers_governed_tools() -> None:
    registry = ToolRegistry()
    registration = await register_stdio_mcp_server(registry, _config())
    try:
        # kebab-case name is registered under a valid-identifier manifest name...
        handler_name = namespaced_tool_name("everything", "get-sum")
        assert handler_name == "mcp__everything__get_sum"
        entry = registry.get(handler_name)
        assert entry is not None
        # ...and invoking it proxies to the raw tools/call name.
        out = await entry.handler({"a": 7, "b": 5})
        assert out["is_error"] is False
        assert "12" in out["text"]
        assert entry.manifest.metadata["descriptor_provenance"]["tool_name"] == "get-sum"
    finally:
        await registration.client.aclose()
