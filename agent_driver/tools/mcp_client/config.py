"""Configuration for outward MCP server connections (opencode-adoption EPIC-06).

Only the **stdio** transport is modelled here — a local MCP server launched as a
subprocess, the dominant transport for local tool servers and the one that needs no
network stack or extra dependency. HTTP/SSE + OAuth are a deferred follow-on (see
``docs/epics/opencode-adoption/EPIC-06-mcp-client.md``); ``McpServerDescriptor.transport``
in ``contracts/mcp_governance`` already reserves ``"http"``/``"sse"`` for them.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True, slots=True)
class StdioServerConfig:
    """A local MCP server the agent connects to over stdio.

    ``server_id`` namespaces the discovered tools (``mcp__<server_id>__<tool>``) and
    must be a short slug. ``command``/``args`` launch the server process; ``env`` is
    passed verbatim (the host resolves any secrets — this config carries no credential
    logic). ``tool_allowlist`` (when set) restricts which discovered tools are
    registered; ``None`` registers all.
    """

    server_id: str
    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] | None = None
    cwd: str | None = None
    tool_allowlist: frozenset[str] | None = None
    init_timeout_seconds: float = 20.0
    request_timeout_seconds: float = 30.0
    # Advertised to the server in the initialize handshake; purely informational.
    client_name: str = "agent-driver"
    client_version: str = "0"
    protocol_version: str = "2025-06-18"
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.server_id or not self.server_id.strip():
            raise ValueError("StdioServerConfig.server_id is required")
        if not self.command or not self.command.strip():
            raise ValueError("StdioServerConfig.command is required")
        if self.init_timeout_seconds <= 0 or self.request_timeout_seconds <= 0:
            raise ValueError("timeouts must be > 0")


__all__ = ["StdioServerConfig"]
