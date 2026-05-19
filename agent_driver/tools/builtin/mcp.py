"""Readonly MCP-style wrappers over static descriptor/resource fixtures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent_driver.contracts import (
    ApprovalMode,
    SideEffectClass,
    ToolManifest,
    ToolRisk,
)
from agent_driver.tools.registry import ToolRegistry

_MCP_TOOL = "mcp_tool"
_MCP_LIST_RESOURCES_TOOL = "mcp_list_resources"
_MCP_READ_RESOURCE_TOOL = "mcp_read_resource"
_MCP_AUTH_TOOL = "mcp_auth"


@dataclass(frozen=True, slots=True)
class _McpToolDescriptor:
    server: str
    tool_name: str
    description: str
    args_schema: dict[str, Any]


@dataclass(frozen=True, slots=True)
class _McpResourceDescriptor:
    server: str
    resource_uri: str
    name: str
    mime_type: str
    content: str


_MCP_TOOL_DESCRIPTORS: dict[tuple[str, str], _McpToolDescriptor] = {
    (
        "demo-docs",
        "search_docs",
    ): _McpToolDescriptor(
        server="demo-docs",
        tool_name="search_docs",
        description="Search demo docs index by keyword.",
        args_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
    ),
    (
        "demo-ops",
        "list_jobs",
    ): _McpToolDescriptor(
        server="demo-ops",
        tool_name="list_jobs",
        description="List latest demo jobs.",
        args_schema={
            "type": "object",
            "properties": {"limit": {"type": "integer", "minimum": 1, "maximum": 50}},
            "additionalProperties": False,
        },
    ),
}

_MCP_RESOURCE_DESCRIPTORS: dict[tuple[str, str], _McpResourceDescriptor] = {
    (
        "demo-docs",
        "resource://docs/quickstart",
    ): _McpResourceDescriptor(
        server="demo-docs",
        resource_uri="resource://docs/quickstart",
        name="Quickstart",
        mime_type="text/markdown",
        content="# Quickstart\nUse search_docs for keyword lookup.",
    ),
    (
        "demo-ops",
        "resource://jobs/latest",
    ): _McpResourceDescriptor(
        server="demo-ops",
        resource_uri="resource://jobs/latest",
        name="Latest Jobs",
        mime_type="application/json",
        content='{"jobs":[{"id":"job-1","status":"completed"}]}',
    ),
}

_MCP_AUTH_STATE: dict[str, dict[str, Any]] = {}


def register_mcp_tools(registry: ToolRegistry) -> None:
    """Register readonly MCP wrappers."""
    registry.register(_mcp_tool_manifest(), _mcp_tool_handler)
    registry.register(_mcp_list_resources_manifest(), _mcp_list_resources_handler)
    registry.register(_mcp_read_resource_manifest(), _mcp_read_resource_handler)
    registry.register(_mcp_auth_manifest(), _mcp_auth_handler)


def _mcp_tool_manifest() -> ToolManifest:
    return ToolManifest(
        name=_MCP_TOOL,
        description="Invoke readonly MCP tool by server and tool name.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=6000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "tool_name": {"type": "string"},
                "arguments": {"type": "object"},
            },
            "required": ["server", "tool_name"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _mcp_list_resources_manifest() -> ToolManifest:
    return ToolManifest(
        name=_MCP_LIST_RESOURCES_TOOL,
        description="List available MCP resources for one server.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=6000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {"server": {"type": "string"}},
            "required": ["server"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _mcp_read_resource_manifest() -> ToolManifest:
    return ToolManifest(
        name=_MCP_READ_RESOURCE_TOOL,
        description="Read one MCP resource content by server and URI.",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=10.0,
        output_char_budget=6000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "resource_uri": {"type": "string"},
                "max_chars": {"type": "integer", "minimum": 64, "maximum": 100_000},
            },
            "required": ["server", "resource_uri"],
            "additionalProperties": False,
        },
        output_type="json",
    )


def _mcp_auth_manifest() -> ToolManifest:
    return ToolManifest(
        name=_MCP_AUTH_TOOL,
        description="Configure MCP server authentication via token or OAuth stub flow.",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.ON_POLICY_MATCH,
        timeout_seconds=10.0,
        output_char_budget=6000,
        idempotent=False,
        args_schema={
            "type": "object",
            "properties": {
                "server": {"type": "string"},
                "mode": {"type": "string", "enum": ["token", "oauth"]},
                "token": {"type": "string"},
                "scopes": {"type": "array", "items": {"type": "string"}},
                "authorize_url": {"type": "string"},
            },
            "required": ["server", "mode"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _mcp_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    tool_name = _required_str(args.get("tool_name"), field="tool_name")
    descriptor = _lookup_tool_descriptor(server=server, tool_name=tool_name)
    arguments = args.get("arguments")
    if arguments is None:
        arguments = {}
    if not isinstance(arguments, dict):
        raise ValueError("arguments must be an object")
    return {
        "summary": f"mcp_tool invoked: {server}/{tool_name}",
        "server": server,
        "tool_name": tool_name,
        "description": descriptor.description,
        "args_schema": descriptor.args_schema,
        "arguments": arguments,
        "provenance": {"server": server, "tool_name": tool_name, "readonly": True},
    }


async def _mcp_list_resources_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    rows = [
        _resource_descriptor_payload(item)
        for item in _MCP_RESOURCE_DESCRIPTORS.values()
        if item.server == server
    ]
    if not rows:
        raise ValueError(f"unknown MCP server resources: {server}")
    return {
        "summary": f"{len(rows)} MCP resources on server '{server}'",
        "server": server,
        "resources": rows,
    }


async def _mcp_read_resource_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    resource_uri = _required_str(args.get("resource_uri"), field="resource_uri")
    max_chars = _as_int(args.get("max_chars"), default=4000, minimum=64)
    descriptor = _lookup_resource_descriptor(server=server, resource_uri=resource_uri)
    content = descriptor.content[:max_chars]
    return {
        "summary": f"mcp resource read: {server} {resource_uri}",
        "server": server,
        "resource": {
            "resource_uri": descriptor.resource_uri,
            "name": descriptor.name,
            "mime_type": descriptor.mime_type,
            "content": content,
            "truncated": len(descriptor.content) > max_chars,
        },
        "provenance": {
            "server": server,
            "resource_uri": resource_uri,
            "readonly": True,
        },
    }


async def _mcp_auth_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    mode = _required_str(args.get("mode"), field="mode")
    if mode not in {"token", "oauth"}:
        raise ValueError("mode must be one of: token, oauth")
    if mode == "token":
        token = _required_str(args.get("token"), field="token")
        scopes = _normalize_scopes(args.get("scopes"))
        _MCP_AUTH_STATE[server] = {
            "server": server,
            "mode": mode,
            "status": "configured",
            "token_hint": _token_hint(token),
            "scopes": scopes,
        }
        return {
            "summary": f"mcp auth configured for '{server}' via token",
            "auth": _MCP_AUTH_STATE[server],
        }
    scopes = _normalize_scopes(args.get("scopes"))
    authorize_url_raw = args.get("authorize_url")
    if authorize_url_raw is None:
        authorize_url = f"https://auth.example/mcp/{server}"
    else:
        authorize_url = _required_str(authorize_url_raw, field="authorize_url")
    _MCP_AUTH_STATE[server] = {
        "server": server,
        "mode": mode,
        "status": "pending_user_consent",
        "authorize_url": authorize_url,
        "scopes": scopes,
    }
    return {
        "summary": f"mcp oauth flow prepared for '{server}'",
        "auth": _MCP_AUTH_STATE[server],
    }


def _lookup_tool_descriptor(*, server: str, tool_name: str) -> _McpToolDescriptor:
    descriptor = _MCP_TOOL_DESCRIPTORS.get((server, tool_name))
    if descriptor is None:
        raise ValueError(f"unknown MCP tool: {server}/{tool_name}")
    return descriptor


def _lookup_resource_descriptor(
    *, server: str, resource_uri: str
) -> _McpResourceDescriptor:
    descriptor = _MCP_RESOURCE_DESCRIPTORS.get((server, resource_uri))
    if descriptor is None:
        raise ValueError(f"unknown MCP resource: {server} {resource_uri}")
    return descriptor


def _resource_descriptor_payload(row: _McpResourceDescriptor) -> dict[str, str]:
    return {
        "resource_uri": row.resource_uri,
        "name": row.name,
        "mime_type": row.mime_type,
    }


def _required_str(raw: Any, *, field: str) -> str:
    value = str(raw or "").strip()
    if not value:
        raise ValueError(f"{field} is required")
    return value


def _as_int(raw: Any, *, default: int, minimum: int) -> int:
    if raw is None:
        return default
    value = int(raw)
    if value < minimum:
        raise ValueError(f"value must be >= {minimum}")
    return value


def _token_hint(token: str) -> str:
    if len(token) <= 4:
        return "*" * len(token)
    return f"{token[:2]}***{token[-2:]}"


def _normalize_scopes(raw: Any) -> list[str]:
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise ValueError("scopes must be an array of strings")
    normalized: list[str] = []
    for item in raw:
        scope = str(item).strip()
        if not scope:
            continue
        normalized.append(scope)
    return normalized


def _reset_mcp_auth_state_for_tests() -> None:
    """Reset in-memory MCP auth state for deterministic tests."""
    _MCP_AUTH_STATE.clear()


__all__ = ["register_mcp_tools"]
