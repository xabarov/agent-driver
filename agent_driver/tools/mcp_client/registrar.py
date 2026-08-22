"""Register a live MCP server's tools into a ToolRegistry (opencode-adoption EPIC-06).

Connects a :class:`StdioMcpClient`, discovers the server's tools via ``tools/list``, and
registers each one as a governed :class:`ToolManifest` under a namespaced name
(``mcp__<server_id>__<tool>``) whose handler proxies to ``tools/call``. Discovered tools
are ``EXTERNAL_ACTION`` / ``MEDIUM`` risk / ``ON_POLICY_MATCH`` approval by default — an
untrusted third-party server never runs unattended under a permissive policy.

The caller owns the returned client's lifecycle (call ``aclose()`` on shutdown).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.mcp_client.config import StdioServerConfig
from agent_driver.tools.mcp_client.stdio_client import StdioMcpClient
from agent_driver.tools.registry.types import ToolHandler

_NAME_PREFIX = "mcp"
_INVALID_NAME_CHARS = re.compile(r"[^A-Za-z0-9_]")


def _sanitize_name_part(part: str) -> str:
    """Coerce one name segment to valid-Python-identifier chars.

    Real MCP servers commonly use kebab-case / dotted tool names (``get-sum``,
    ``fs.read``); the runtime's manifest name must be a valid Python identifier for
    code-agent compatibility, so non-identifier characters collapse to ``_``. Only the
    registered *manifest* name is sanitized — the original tool name is preserved for the
    actual ``tools/call`` (see ``register_stdio_mcp_server``).
    """
    cleaned = _INVALID_NAME_CHARS.sub("_", part.strip())
    return cleaned or "_"


def namespaced_tool_name(server_id: str, tool_name: str) -> str:
    """``mcp__<server_id>__<tool>`` — stable across servers, identifier-safe.

    Both segments are sanitized to identifier characters; the ``mcp`` prefix guarantees a
    letter start, so the whole name is always a valid Python identifier.
    """
    return (
        f"{_NAME_PREFIX}__{_sanitize_name_part(server_id)}"
        f"__{_sanitize_name_part(tool_name)}"
    )


@dataclass(frozen=True, slots=True)
class McpRegistration:
    """Result of wiring one MCP server into a registry."""

    server_id: str
    client: StdioMcpClient
    tool_names: tuple[str, ...]
    server_info: dict[str, Any]


def _permissive_schema() -> dict[str, Any]:
    return {"type": "object", "additionalProperties": True}


def _tool_manifest(server_id: str, descriptor: dict[str, Any]) -> ToolManifest:
    tool_name = str(descriptor.get("name") or "").strip()
    schema = descriptor.get("inputSchema")
    args_schema = schema if isinstance(schema, dict) and schema else _permissive_schema()
    description = str(
        descriptor.get("description") or f"MCP tool {tool_name} on {server_id}"
    )
    return ToolManifest(
        name=namespaced_tool_name(server_id, tool_name),
        description=description,
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=30.0,
        output_char_budget=8000,
        idempotent=False,
        args_schema=args_schema,
        output_type="json",
        metadata={
            "descriptor_provenance": {
                "inventory_source": "mcp_stdio",
                "descriptor_kind": "mcp_tool",
                "server_id": server_id,
                "tool_name": tool_name,
            },
            "security_policy": {
                "approval_gate": "on_policy_match",
                "external_server": True,
            },
        },
    )


def normalize_call_result(result: dict[str, Any]) -> dict[str, Any]:
    """Flatten an MCP ``tools/call`` result into the runtime tool-output shape.

    Joins text content blocks into ``text`` for the model, preserves the raw ``content``
    blocks and any ``structuredContent``, and surfaces the tool-level ``isError`` flag
    (distinct from a transport/protocol failure, which raises before we get here).
    """
    content = result.get("content")
    blocks = content if isinstance(content, list) else []
    text_parts = [
        str(block.get("text", ""))
        for block in blocks
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    is_error = bool(result.get("isError"))
    joined = "\n".join(part for part in text_parts if part)
    return {
        "summary": joined[:200] if joined else ("mcp tool error" if is_error else "ok"),
        "text": joined,
        "is_error": is_error,
        "content": blocks,
        "structured": result.get("structuredContent"),
    }


def _make_handler(client: Any, tool_name: str) -> ToolHandler:
    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        result = await client.call_tool(tool_name, args)
        return normalize_call_result(result)

    return _handler


async def register_mcp_client(
    registry: Any, client: Any, *, server_id: str, tool_allowlist: Any = None
) -> McpRegistration:
    """Transport-agnostic core: connect ``client``, discover its tools, and register each
    as a governed namespaced ToolManifest into ``registry``.

    Works with any client exposing ``start()``/``list_tools()``/``call_tool()``/
    ``server_info``/``aclose()`` (stdio or HTTP). Honors ``tool_allowlist`` (``None`` =
    all). On any connect/discovery failure the client is closed before the error
    propagates so a half-open connection/subprocess is never leaked. The caller owns the
    returned client's lifecycle.
    """
    try:
        await client.start()
        discovered = await client.list_tools()
    except BaseException:
        await client.aclose()
        raise
    registered: list[str] = []
    for descriptor in discovered:
        tool_name = str(descriptor.get("name") or "").strip()
        if not tool_name:
            continue
        if tool_allowlist is not None and tool_name not in tool_allowlist:
            continue
        manifest = _tool_manifest(server_id, descriptor)
        registry.register(manifest, _make_handler(client, tool_name))
        registered.append(manifest.name)
    return McpRegistration(
        server_id=server_id,
        client=client,
        tool_names=tuple(registered),
        server_info=client.server_info,
    )


async def resync_mcp_server_tools(
    registry: Any, registration: McpRegistration, *, tool_allowlist: Any = None
) -> McpRegistration:
    """Re-discover an already-connected server's tools and reconcile the registry (EPIC-06).

    Call this after the client's ``tools_changed_event`` fires
    (``notifications/tools/list_changed``): re-runs ``tools/list``, re-registers current
    tools (``register`` overwrites in place), and **unregisters** tools the server dropped.
    Returns an updated :class:`McpRegistration` (same client, new ``tool_names``); clears the
    client's change event when present.
    """
    client = registration.client
    discovered = await client.list_tools()
    new_names: set[str] = set()
    for descriptor in discovered:
        tool_name = str(descriptor.get("name") or "").strip()
        if not tool_name:
            continue
        if tool_allowlist is not None and tool_name not in tool_allowlist:
            continue
        manifest = _tool_manifest(registration.server_id, descriptor)
        registry.register(manifest, _make_handler(client, tool_name))
        new_names.add(manifest.name)
    unregister = getattr(registry, "unregister", None)
    if callable(unregister):
        for stale in set(registration.tool_names) - new_names:
            unregister(stale)
    event = getattr(client, "_tools_changed", None)
    if event is not None:
        event.clear()
    return McpRegistration(
        server_id=registration.server_id,
        client=client,
        tool_names=tuple(sorted(new_names)),
        server_info=registration.server_info,
    )


async def register_stdio_mcp_server(
    registry: Any, config: StdioServerConfig
) -> McpRegistration:
    """Connect, discover, and register a **stdio** MCP server's tools into ``registry``."""
    return await register_mcp_client(
        registry,
        StdioMcpClient(config),
        server_id=config.server_id,
        tool_allowlist=config.tool_allowlist,
    )


async def register_http_mcp_server(registry: Any, config: Any) -> McpRegistration:
    """Connect, discover, and register a **streamable-HTTP** MCP server's tools.

    ``config`` is an :class:`~agent_driver.tools.mcp_client.config.HttpServerConfig`.
    Imported lazily so the HTTP client (and its ``httpx`` use) is only loaded when a host
    actually wires an HTTP server.
    """
    from agent_driver.tools.mcp_client.http_client import HttpMcpClient  # noqa: PLC0415

    return await register_mcp_client(
        registry,
        HttpMcpClient(config),
        server_id=config.server_id,
        tool_allowlist=config.tool_allowlist,
    )


__all__ = [
    "McpRegistration",
    "namespaced_tool_name",
    "normalize_call_result",
    "register_http_mcp_server",
    "register_mcp_client",
    "register_stdio_mcp_server",
    "resync_mcp_server_tools",
]
