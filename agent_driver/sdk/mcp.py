"""SDK wiring for outward MCP servers (opencode-adoption EPIC-06).

Connect a host-declared list of MCP servers (stdio and/or streamable-HTTP) into a built
:class:`~agent_driver.sdk.agent.Agent`, registering each server's discovered tools into
the agent's live tool registry so the model can call them. Connection is async (handshake +
`tools/list`), which is why it is a post-build step rather than a `create_agent` argument.

Typical use::

    agent = create_agent(provider=provider)
    regs = await connect_mcp_servers(agent, [
        StdioServerConfig(server_id="fs", command="mcp-fs", args=("/data",)),
        HttpServerConfig(server_id="docs", url="https://host/mcp"),
    ])
    try:
        await agent.run(...)
    finally:
        await close_mcp_servers(regs)

The caller owns the returned registrations' lifecycle — call :func:`close_mcp_servers` on
shutdown to terminate the subprocesses / HTTP clients.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING

from agent_driver.tools.mcp_client import (
    HttpServerConfig,
    McpRegistration,
    StdioServerConfig,
    register_http_mcp_server,
    register_stdio_mcp_server,
)

if TYPE_CHECKING:
    from agent_driver.sdk.agent import Agent


async def connect_mcp_servers(
    agent: "Agent",
    configs: Iterable[StdioServerConfig | HttpServerConfig],
) -> list[McpRegistration]:
    """Connect each declared MCP server and register its tools into ``agent``.

    Dispatches by config type (stdio vs streamable-HTTP), registering the discovered tools
    as governed, namespaced (``mcp__<server>__<tool>``) entries in the agent's live
    registry. Returns one :class:`McpRegistration` per server (holding the connected
    client + registered tool names) for the caller to close on shutdown. Raises
    ``TypeError`` on an unsupported config; already-connected servers are left registered
    (the caller closes what it got back).
    """
    registry = agent._runner.deps.tool_registry
    registrations: list[McpRegistration] = []
    for config in configs:
        if isinstance(config, StdioServerConfig):
            registrations.append(await register_stdio_mcp_server(registry, config))
        elif isinstance(config, HttpServerConfig):
            registrations.append(await register_http_mcp_server(registry, config))
        else:
            raise TypeError(
                f"unsupported MCP server config: {type(config).__name__}"
            )
    return registrations


async def close_mcp_servers(registrations: Iterable[McpRegistration]) -> None:
    """Close every connected MCP client (idempotent per client). Never raises."""
    for registration in registrations:
        try:
            await registration.client.aclose()
        except Exception:  # noqa: BLE001 - shutdown best-effort; one bad client
            continue  # ...must not block closing the rest


__all__ = ["close_mcp_servers", "connect_mcp_servers"]
