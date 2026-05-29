"""Tests for plan panel text rendering."""

from __future__ import annotations

from agent_driver.cli.tui.plan_panel import format_plan_panel, plan_progress_footer


def test_format_plan_panel_renders_status_icons() -> None:
    snapshot = {
        "todos": [
            {"id": "a", "content": "Done step", "status": "completed"},
            {"id": "b", "content": "Active step", "status": "in_progress"},
            {"id": "c", "content": "Later", "status": "pending"},
        ],
        "completed": 1,
        "total": 3,
        "in_progress_id": "b",
        "in_progress_index": 2,
        "plan_title": "Active step",
    }
    text = format_plan_panel(snapshot)
    assert "Plan · 1/3 done" in text
    assert "step 2/3" in text
    assert "active: Active step" in text
    assert "✓" in text
    assert "■" in text
    assert "□" in text
    assert "Active step" in text


def test_plan_progress_footer_shows_active_at_zero_done() -> None:
    snapshot = {
        "todos": [{"id": "b", "content": "Fetch sources", "status": "in_progress"}],
        "completed": 0,
        "total": 1,
        "in_progress_index": 1,
        "plan_title": "Fetch sources",
    }
    progress, current = plan_progress_footer(snapshot)
    assert progress == "plan 0/1"
    assert current == "Fetch sources"
