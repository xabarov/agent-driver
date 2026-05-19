"""Tests for built-in + MCP tool pool merge helpers."""

from __future__ import annotations

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools import ToolRegistry, assemble_tool_pool, get_merged_tools


async def _noop_handler(_args: dict[str, object]) -> dict[str, object]:
    return {"summary": "ok"}


def _manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"tool {name}",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
    )


def test_assemble_tool_pool_prefers_builtin_and_applies_deny_patterns() -> None:
    """Built-in tool should win over MCP name collision and deny should filter."""
    builtin = ToolRegistry()
    builtin.register(_manifest("alpha"), _noop_handler)
    builtin.register(_manifest("shared"), _noop_handler)
    mcp = ToolRegistry()
    mcp.register(_manifest("shared"), _noop_handler)
    mcp.register(_manifest("mcp_remote"), _noop_handler)
    merged = assemble_tool_pool(
        builtin_registry=builtin,
        mcp_registry=mcp,
        denied_tools=("mcp_*",),
    )
    assert merged.list_names() == ["alpha", "shared"]


def test_get_merged_tools_returns_deterministic_manifest_order() -> None:
    """Merged manifests should be sorted and deterministic."""
    builtin = ToolRegistry()
    builtin.register(_manifest("zeta"), _noop_handler)
    builtin.register(_manifest("alpha"), _noop_handler)
    manifests = get_merged_tools(builtin_registry=builtin)
    assert [item.name for item in manifests] == ["alpha", "zeta"]
