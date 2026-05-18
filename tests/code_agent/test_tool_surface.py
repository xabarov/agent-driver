"""CodeAgent callable tool-surface tests."""

from __future__ import annotations

from agent_driver.code_agent import (
    build_callable_tool_surface,
    render_callable_tool_docs,
)
from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.registry import ToolRegistry


async def _lookup(args: dict[str, object]) -> dict[str, object]:
    return {"summary": str(args.get("query", ""))}


def test_callable_tool_surface_is_deterministic() -> None:
    """Callable tool descriptors should be stable and sorted."""
    registry = ToolRegistry()
    registry.register(
        ToolManifest(
            name="lookup",
            description="Lookup by query",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            args_schema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
        _lookup,
    )
    specs = build_callable_tool_surface(registry)
    assert len(specs) == 1
    assert specs[0].name == "lookup"
    assert "query: object" in specs[0].signature
    docs = render_callable_tool_docs(specs)
    assert "def lookup(" in docs
    assert "Lookup by query" in docs
