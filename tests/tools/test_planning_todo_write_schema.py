"""Schema-level tests for todo_write planning tool."""

from __future__ import annotations

from agent_driver.tools.planning import register_planning_tool
from agent_driver.tools.registry import ToolRegistry


def test_todo_write_schema_declares_status_enum_and_required_fields() -> None:
    """Manifest schema should expose strict todo item structure.

    Note: ``content`` is intentionally NOT in ``required`` because
    ``merge=true`` status updates target existing todos by ``id`` only
    (see the schema's per-field description in ``planning.py``).
    """
    registry = ToolRegistry()
    register_planning_tool(registry)
    row = registry.get("todo_write")
    assert row is not None
    schema = row.manifest.args_schema
    assert isinstance(schema, dict)
    todos = schema["properties"]["todos"]
    items = todos["items"]
    assert items["required"] == ["id", "status"]
    # ``content`` must still be DECLARED as a property even though it's
    # not required — merge-mode updates rely on the field being known.
    assert "content" in items["properties"]
    assert items["properties"]["status"]["enum"] == [
        "pending",
        "in_progress",
        "completed",
        "cancelled",
    ]
