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


@dataclass(frozen=True, slots=True)
class HttpServerConfig:
    """A remote MCP server the agent connects to over the **streamable-HTTP** transport.

    ``url`` is the single MCP endpoint (JSON-RPC POST target, e.g.
    ``https://host/mcp``). ``headers`` carries auth/bearer tokens verbatim — the host
    resolves any secret into it; this config holds no credential logic. The client
    manages the ``Mcp-Session-Id`` returned by the initialize handshake automatically.
    ``tool_allowlist`` (when set) restricts which discovered tools are registered.
    """

    server_id: str
    url: str
    headers: dict[str, str] | None = None
    tool_allowlist: frozenset[str] | None = None
    init_timeout_seconds: float = 30.0
    request_timeout_seconds: float = 60.0
    client_name: str = "agent-driver"
    client_version: str = "0"
    protocol_version: str = "2025-06-18"
    verify_tls: bool = True
    metadata: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.server_id or not self.server_id.strip():
            raise ValueError("HttpServerConfig.server_id is required")
        if not self.url or not self.url.strip():
            raise ValueError("HttpServerConfig.url is required")
        if not self.url.startswith(("http://", "https://")):
            raise ValueError("HttpServerConfig.url must be an http(s) URL")
        if self.init_timeout_seconds <= 0 or self.request_timeout_seconds <= 0:
            raise ValueError("timeouts must be > 0")


__all__ = ["HttpServerConfig", "StdioServerConfig"]
