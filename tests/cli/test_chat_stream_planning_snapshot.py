"""Tests for planning snapshot extraction in chat stream."""

from __future__ import annotations

import pytest

from agent_driver.cli.chat_stream import (
    _extract_planning_snapshot,
    _summarize_tool_args,
    render_chat_stream,
)
from agent_driver.contracts.stream import RunStreamEvent


def test_extract_planning_snapshot_from_tool_completed() -> None:
    event = RunStreamEvent(
        stream_id="run1:1",
        run_id="run1",
        attempt_id="attempt1",
        seq=1,
        event="tool_call_completed",
        data={
            "planning_snapshot": {
                "todos": [{"id": "s1", "content": "Search", "status": "in_progress"}],
                "completed": 0,
                "total": 1,
            }
        },
    )
    snapshot = _extract_planning_snapshot(event)
    assert snapshot is not None
    assert snapshot["total"] == 1


def test_summarize_todo_write_args_is_compact() -> None:
    summary = _summarize_tool_args(
        {
            "merge": False,
            "todos": [
                {"id": "s1", "content": "Search web", "status": "in_progress"},
                {"id": "s2", "content": "Summarize", "status": "pending"},
            ],
        },
        tool_name="todo_write",
    )
    assert "merge=false" in summary
    assert "s1[in_progress]" in summary
    assert "todos=[" not in summary


@pytest.mark.asyncio
async def test_render_chat_stream_calls_refresh_plan_panel() -> None:
    snapshot = {
        "todos": [{"id": "s1", "content": "Step", "status": "pending"}],
        "completed": 0,
        "total": 1,
    }

    class _FakeRenderer:
        rich_enabled = False
        live_console = None
        panels: list[dict[str, object]] = []

        def assistant_prefix(self) -> str:
            return ""

        def emit_assistant_delta(self, delta: str) -> None:
            _ = delta

        def emit_assistant_tail(self, text: str) -> None:
            _ = text

        def emit_tool_card(self, **kwargs: object) -> None:
            _ = kwargs

        def emit_warning(self, compact: str) -> None:
            _ = compact

        def emit_event(self, compact: str) -> None:
            _ = compact

        def emit_run_summary(
            self,
            run_id: str,
            tools_used: int,
            warnings_seen: int,
            duration_seconds: float | None = None,
        ) -> None:
            _ = (run_id, tools_used, warnings_seen, duration_seconds)

        def emit_raw(self, text: str) -> None:
            _ = text

        def refresh_plan_panel(self, panel: dict[str, object] | None) -> None:
            if panel is not None:
                self.panels.append(panel)

        def clear_plan_panel(self) -> None:
            self.panels.clear()

    async def _stream():
        yield RunStreamEvent(
            stream_id="run1:1",
            run_id="run1",
            attempt_id="attempt1",
            seq=1,
            event="tool_call_completed",
            data={
                "tools": [
                    {
                        "tool_name": "todo_write",
                        "args": {"todos": [{"id": "s1", "content": "Step", "status": "pending"}]},
                        "status": "completed",
                        "result_summary": "todo_write applied 1 rows",
                    }
                ],
                "planning_snapshot": snapshot,
            },
        )
        yield RunStreamEvent(
            stream_id="run1:2",
            run_id="run1",
            attempt_id="attempt1",
            seq=2,
            event="run_completed",
            data={},
        )

    renderer = _FakeRenderer()
    lines: list[str] = []

    await render_chat_stream(
        stream=_stream(),
        output=lines.append,
        run_id="run1",
        renderer=renderer,
        animate=False,
    )
    assert len(renderer.panels) == 1
    assert renderer.panels[0]["total"] == 1
