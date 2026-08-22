"""Bridge ACP ``mcp_servers`` session params to the outward MCP client (EPIC-06).

The ACP protocol lets a client declare MCP servers per session (``McpServerStdio`` /
``McpServerHttp``). :func:`acp_mcp_configs` translates those descriptors into the runtime's
:class:`StdioServerConfig` / :class:`HttpServerConfig`; :func:`connect_acp_mcp_servers`
connects the not-yet-connected ones into an agent, deduped by ``server_id``.

Limitation: the ACP adapter binds one shared :class:`Agent` across all sessions, so
declared servers are connected **once** into that shared tool registry and live for the
adapter's lifetime (no per-session isolation/teardown — the ACP contract's per-session
scoping is not modelled by a single-agent adapter).
"""

from __future__ import annotations

from typing import Any

from agent_driver.tools.mcp_client import (
    HttpServerConfig,
    McpRegistration,
    StdioServerConfig,
    register_http_mcp_server,
    register_stdio_mcp_server,
)


def _pairs_to_dict(items: Any) -> dict[str, str]:
    """ACP env/headers are lists of ``{name, value}`` (or already a dict)."""
    if isinstance(items, dict):
        return {str(k): str(v) for k, v in items.items()}
    out: dict[str, str] = {}
    for item in items or ():
        name = getattr(item, "name", None)
        value = getattr(item, "value", None)
        if name is None and isinstance(item, dict):
            name, value = item.get("name"), item.get("value")
        if name is not None:
            out[str(name)] = str(value if value is not None else "")
    return out


def acp_mcp_configs(
    mcp_servers: Any,
) -> list[StdioServerConfig | HttpServerConfig]:
    """Translate ACP ``mcp_servers`` descriptors into runtime MCP server configs.

    Recognises stdio descriptors (a ``command``) and HTTP descriptors (a ``url``); any
    other kind (e.g. ACP SSE, unsupported by our client) is skipped.
    """
    configs: list[StdioServerConfig | HttpServerConfig] = []
    for server in mcp_servers or ():
        name = str(getattr(server, "name", "") or "").strip()
        command = getattr(server, "command", None)
        url = getattr(server, "url", None)
        if not name:
            continue
        if command:
            configs.append(
                StdioServerConfig(
                    server_id=name,
                    command=str(command),
                    args=tuple(str(a) for a in (getattr(server, "args", None) or ())),
                    env=_pairs_to_dict(getattr(server, "env", None)) or None,
                )
            )
        elif url:
            configs.append(
                HttpServerConfig(
                    server_id=name,
                    url=str(url),
                    headers=_pairs_to_dict(getattr(server, "headers", None)) or None,
                )
            )
    return configs


async def connect_acp_mcp_servers(
    agent: Any,
    mcp_servers: Any,
    *,
    already_connected: dict[str, McpRegistration],
) -> list[McpRegistration]:
    """Connect the declared ACP servers not already registered (deduped by ``server_id``).

    Mutates ``already_connected`` with the new registrations and returns just the newly
    connected ones. A server that fails to connect is skipped (non-fatal): a bad
    client-declared server must not break session creation.
    """
    configs = [
        cfg
        for cfg in acp_mcp_configs(mcp_servers)
        if cfg.server_id not in already_connected
    ]
    registry = agent._runner.deps.tool_registry
    new: list[McpRegistration] = []
    for cfg in configs:
        try:
            if isinstance(cfg, StdioServerConfig):
                registration = await register_stdio_mcp_server(registry, cfg)
            else:
                registration = await register_http_mcp_server(registry, cfg)
        except Exception:  # noqa: BLE001 - a bad declared server must not break sessions
            continue
        already_connected[registration.server_id] = registration
        new.append(registration)
    return new


__all__ = ["acp_mcp_configs", "connect_acp_mcp_servers"]
