"""Schema-level tests for todo_write planning tool."""

from __future__ import annotations

from agent_driver.tools.planning import register_planning_tool
from agent_driver.tools.registry import ToolRegistry


def test_todo_write_schema_declares_status_enum_and_required_fields() -> None:
    """Manifest schema should expose strict todo item structure."""
    registry = ToolRegistry()
    register_planning_tool(registry)
    row = registry.get("todo_write")
    assert row is not None
    schema = row.manifest.args_schema
    assert isinstance(schema, dict)
    todos = schema["properties"]["todos"]
    items = todos["items"]
    assert items["required"] == ["id", "content", "status"]
    assert items["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "completed",
        "cancelled",
    ]
