"""Deterministic built-in + MCP tool pool merge helpers."""

from __future__ import annotations

import fnmatch
from typing import Iterable

from agent_driver.contracts.tools import ToolManifest
from agent_driver.tools.registry import ToolRegistry


async def _unavailable_external_tool_handler(_args: dict[str, object]) -> dict[str, object]:
    raise ValueError("external merged tool has no local handler")


def assemble_tool_pool(
    *,
    builtin_registry: ToolRegistry,
    mcp_registry: ToolRegistry | None = None,
    denied_tools: Iterable[str] | None = None,
) -> ToolRegistry:
    """Merge built-in and MCP registries with deny-rules and stable ordering."""
    denied_patterns = tuple(item.strip() for item in (denied_tools or ()) if item.strip())
    merged = ToolRegistry()
    names = set(builtin_registry.list_names())
    if mcp_registry is not None:
        names.update(mcp_registry.list_names())
    for name in sorted(names):
        if _is_denied(name, denied_patterns):
            continue
        builtin = builtin_registry.get(name)
        if builtin is not None:
            merged.register(builtin.manifest, builtin.handler)
            continue
        if mcp_registry is None:
            continue
        mcp = mcp_registry.get(name)
        if mcp is not None:
            # Keep manifest for prompt/docs and provide explicit local-unavailable handler.
            merged.register(mcp.manifest, _unavailable_external_tool_handler)
    return merged


def get_merged_tools(
    *,
    builtin_registry: ToolRegistry,
    mcp_registry: ToolRegistry | None = None,
    denied_tools: Iterable[str] | None = None,
) -> list[ToolManifest]:
    """Return merged manifests with deterministic ordering."""
    merged = assemble_tool_pool(
        builtin_registry=builtin_registry,
        mcp_registry=mcp_registry,
        denied_tools=denied_tools,
    )
    return [row.manifest for row in merged.list_registered()]


def _is_denied(name: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch.fnmatch(name, pattern) for pattern in patterns)


__all__ = ["assemble_tool_pool", "get_merged_tools"]
