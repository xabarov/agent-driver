"""CodeAgent callable tool-surface tests."""

from __future__ import annotations

from agent_driver.code_agent import (
    build_callable_tool_surface,
    render_callable_tool_docs,
)
from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools import register_builtin_tools
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
    assert "query: str" in specs[0].signature
    assert specs[0].required_args == ("query",)
    assert specs[0].optional_args == ()
    docs = render_callable_tool_docs(specs)
    assert "def lookup(" in docs
    assert "Lookup by query" in docs
    assert "required=['query'], optional=[]" in docs


def test_callable_tool_surface_maps_builtin_types() -> None:
    """Builtin tool schemas should map to practical Python annotations."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    specs = {item.name: item for item in build_callable_tool_surface(registry)}
    file_edit = specs["file_edit"]
    tool_search = specs["tool_search"]
    assert "path: str" in file_edit.signature
    assert "expected_occurrences: int = None" in file_edit.signature
    assert "dry_run: bool = None" in file_edit.signature
    assert "query: str = None" in tool_search.signature
    assert "include_schemas: bool = None" in tool_search.signature
    assert "required=['new_text', 'old_text', 'path']" in render_callable_tool_docs(
        [file_edit]
    )
