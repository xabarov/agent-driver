"""Readonly MCP-style wrappers over static descriptor/resource fixtures."""

from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
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


def _load_catalog(path: str) -> tuple[dict[tuple[str, str], _McpToolDescriptor], dict[tuple[str, str], _McpResourceDescriptor]]:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("catalog payload must be object")
    tools_payload = payload.get("tools", [])
    resources_payload = payload.get("resources", [])
    if not isinstance(tools_payload, list) or not isinstance(resources_payload, list):
        raise ValueError("catalog tools/resources must be arrays")
    tools: dict[tuple[str, str], _McpToolDescriptor] = {}
    resources: dict[tuple[str, str], _McpResourceDescriptor] = {}
    for item in tools_payload:
        if not isinstance(item, dict):
            continue
        server = _required_str(item.get("server"), field="server")
        tool_name = _required_str(item.get("tool_name"), field="tool_name")
        tools[(server, tool_name)] = _McpToolDescriptor(
            server=server,
            tool_name=tool_name,
            description=_required_str(item.get("description"), field="description"),
            args_schema=item.get("args_schema") if isinstance(item.get("args_schema"), dict) else {},
        )
    for item in resources_payload:
        if not isinstance(item, dict):
            continue
        server = _required_str(item.get("server"), field="server")
        resource_uri = _required_str(item.get("resource_uri"), field="resource_uri")
        resources[(server, resource_uri)] = _McpResourceDescriptor(
            server=server,
            resource_uri=resource_uri,
            name=_required_str(item.get("name"), field="name"),
            mime_type=_required_str(item.get("mime_type"), field="mime_type"),
            content=str(item.get("content", "")),
        )
    return tools, resources


def _resolve_catalog(args: dict[str, Any]) -> tuple[dict[tuple[str, str], _McpToolDescriptor], dict[tuple[str, str], _McpResourceDescriptor], str]:
    catalog_path_raw = args.get("catalog_json_path")
    if catalog_path_raw is None:
        return _MCP_TOOL_DESCRIPTORS, _MCP_RESOURCE_DESCRIPTORS, "builtin_mcp_fixture"
    catalog_path = _required_str(catalog_path_raw, field="catalog_json_path")
    tools, resources = _load_catalog(catalog_path)
    return tools, resources, f"catalog_json:{catalog_path}"


def _normalize_allowlist(raw: Any) -> set[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        raise ValueError("allowlist must be an array of strings")
    values = {str(item).strip() for item in raw if str(item).strip()}
    return values if values else set()


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
                "catalog_json_path": {"type": "string"},
                "tool_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["server", "tool_name"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "server": {"type": "string"},
                "tool_name": {"type": "string"},
                "description": {"type": "string"},
                "args_schema": {"type": "object"},
                "output_schema": {"type": ["object", "null"]},
                "arguments": {"type": "object"},
                "descriptor_audit": {"type": "object"},
                "provenance": {"type": "object"},
            },
            "required": ["summary", "server", "tool_name", "provenance"],
            "additionalProperties": True,
        },
        metadata={
            "descriptor_provenance": {
                "inventory_source": "builtin static fixtures",
                "descriptor_kind": "mcp_tool",
            },
            "security_policy": {
                "approval_gate": "on_policy_match",
                "allowlist_required": True,
            },
        },
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
            "properties": {
                "server": {"type": "string"},
                "catalog_json_path": {"type": "string"},
                "resource_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["server"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "server": {"type": "string"},
                "resources": {"type": "array"},
                "descriptor_audit": {"type": "object"},
            },
            "required": ["summary", "server", "resources"],
            "additionalProperties": True,
        },
        metadata={
            "descriptor_provenance": {
                "inventory_source": "builtin static fixtures",
                "descriptor_kind": "mcp_resource",
            },
            "security_policy": {
                "approval_gate": "never",
                "allowlist_required": True,
            },
        },
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
                "catalog_json_path": {"type": "string"},
                "resource_allowlist": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": ["server", "resource_uri"],
            "additionalProperties": False,
        },
        output_type="json",
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "server": {"type": "string"},
                "resource": {"type": "object"},
                "provenance": {"type": "object"},
                "descriptor_audit": {"type": "object"},
            },
            "required": ["summary", "server", "resource", "provenance"],
            "additionalProperties": True,
        },
        metadata={
            "descriptor_provenance": {
                "inventory_source": "builtin static fixtures",
                "descriptor_kind": "mcp_resource",
            },
            "security_policy": {
                "approval_gate": "never",
                "allowlist_required": True,
            },
        },
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
        output_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "auth": {"type": "object"},
                "descriptor_audit": {"type": "object"},
            },
            "required": ["summary", "auth"],
            "additionalProperties": True,
        },
        metadata={
            "descriptor_provenance": {
                "inventory_source": "builtin static fixtures",
                "descriptor_kind": "mcp_auth",
            },
            "security_policy": {
                "approval_gate": "on_policy_match",
                "allowlist_required": True,
            },
        },
    )


async def _mcp_tool_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    tool_name = _required_str(args.get("tool_name"), field="tool_name")
    tools_catalog, _resources_catalog, source = _resolve_catalog(args)
    allowlist = _normalize_allowlist(args.get("tool_allowlist"))
    if allowlist is not None and tool_name not in allowlist:
        raise ValueError(f"tool '{tool_name}' not in allowlist")
    descriptor = _lookup_tool_descriptor(
        server=server,
        tool_name=tool_name,
        descriptors=tools_catalog,
    )
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
        "output_schema": {"type": "object"},
        "arguments": arguments,
        "descriptor_audit": {
            "source": source,
            "server": server,
            "tool_name": tool_name,
            "allowlisted": allowlist is not None,
        },
        "provenance": {"server": server, "tool_name": tool_name, "readonly": True},
    }


async def _mcp_list_resources_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    _tools_catalog, resources_catalog, source = _resolve_catalog(args)
    allowlist = _normalize_allowlist(args.get("resource_allowlist"))
    rows = [
        _resource_descriptor_payload(item)
        for item in resources_catalog.values()
        if item.server == server
        and (allowlist is None or item.resource_uri in allowlist)
    ]
    if not rows:
        raise ValueError(f"unknown MCP server resources: {server}")
    return {
        "summary": f"{len(rows)} MCP resources on server '{server}'",
        "server": server,
        "resources": rows,
        "descriptor_audit": {
            "source": source,
            "server": server,
            "resource_count": len(rows),
            "allowlisted": allowlist is not None,
        },
    }


async def _mcp_read_resource_handler(args: dict[str, Any]) -> dict[str, Any]:
    server = _required_str(args.get("server"), field="server")
    resource_uri = _required_str(args.get("resource_uri"), field="resource_uri")
    max_chars = _as_int(args.get("max_chars"), default=4000, minimum=64)
    _tools_catalog, resources_catalog, source = _resolve_catalog(args)
    allowlist = _normalize_allowlist(args.get("resource_allowlist"))
    if allowlist is not None and resource_uri not in allowlist:
        raise ValueError(f"resource '{resource_uri}' not in allowlist")
    descriptor = _lookup_resource_descriptor(
        server=server,
        resource_uri=resource_uri,
        descriptors=resources_catalog,
    )
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
        "descriptor_audit": {
            "source": source,
            "server": server,
            "resource_uri": resource_uri,
            "allowlisted": allowlist is not None,
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
            "descriptor_audit": {
                "source": "builtin_mcp_fixture",
                "server": server,
                "mode": mode,
            },
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
        "descriptor_audit": {
            "source": "builtin_mcp_fixture",
            "server": server,
            "mode": mode,
        },
    }


def _lookup_tool_descriptor(
    *,
    server: str,
    tool_name: str,
    descriptors: dict[tuple[str, str], _McpToolDescriptor] | None = None,
) -> _McpToolDescriptor:
    descriptor = (descriptors or _MCP_TOOL_DESCRIPTORS).get((server, tool_name))
    if descriptor is None:
        raise ValueError(f"unknown MCP tool: {server}/{tool_name}")
    return descriptor


def _lookup_resource_descriptor(
    *,
    server: str,
    resource_uri: str,
    descriptors: dict[tuple[str, str], _McpResourceDescriptor] | None = None,
) -> _McpResourceDescriptor:
    descriptor = (descriptors or _MCP_RESOURCE_DESCRIPTORS).get((server, resource_uri))
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
