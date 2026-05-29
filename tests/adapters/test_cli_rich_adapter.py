"""Tests for optional rich CLI rendering and fallback behavior."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent_driver.adapters import (
    cli_run_live_lines,
    render_cli_line,
    render_cli_live_line,
    render_rich_event_text,
)
from agent_driver.contracts import AgentRunInput, RunStreamEvent
import agent_driver.adapters.cli_rich as cli_rich


def _event(
    *,
    seq: int,
    event: str,
    data: dict[str, object],
) -> RunStreamEvent:
    return RunStreamEvent(
        schema_version="1.0",
        source="runtime_event",
        stream_id=f"run_x:{seq}",
        run_id="run_x",
        attempt_id="att_1",
        seq=seq,
        event=event,
        data=data,
    )


def test_render_rich_event_text_has_vocabulary_for_known_kinds() -> None:
    """Known event categories should render readable labels."""
    lifecycle = render_rich_event_text(
        _event(seq=1, event="run_started", data={"agent_id": "agent"})
    )
    llm = render_rich_event_text(
        _event(
            seq=2,
            event="llm_call_started",
            data={"provider": "openrouter", "model": "x"},
        )
    )
    tool = render_rich_event_text(
        _event(seq=3, event="tool_call_started", data={"tool_name": "web_search"})
    )
    interrupt = render_rich_event_text(
        _event(seq=4, event="interrupt_requested", data={"reason": "approval_required"})
    )
    warning = render_rich_event_text(_event(seq=5, event="warning", data={"kind": "budget"}))
    assert "RUN run_started" in lifecycle
    assert "LLM llm_call_started" in llm
    assert "TOOL tool_call_started" in tool
    assert "INTERRUPT reason=approval_required" in interrupt
    assert "WARNING kind=budget" in warning


def test_render_rich_event_text_truncates_large_payloads() -> None:
    """Large payload previews should be bounded."""
    text = render_rich_event_text(
        _event(seq=1, event="run_started", data={"blob": "x" * 512}),
        max_payload_chars=80,
    )
    assert text.endswith("...")
    assert len(text) < 220


def test_render_rich_event_text_tracks_token_accumulation() -> None:
    """Token delta rendering should maintain cumulative token preview."""
    state: dict[str, str] = {}
    first = render_rich_event_text(
        _event(seq=1, event="token_delta", data={"delta_text": "Hello "}),
        token_state=state,
    )
    second = render_rich_event_text(
        _event(seq=2, event="token_delta", data={"delta_text": "world"}),
        token_state=state,
    )
    assert "total=6" in first
    assert "total=11" in second
    assert "Hello world" in second


def test_render_rich_event_text_unknown_event_fallback() -> None:
    """Unknown event kinds should still produce deterministic output."""
    text = render_rich_event_text(_event(seq=9, event="something_new", data={"k": 1}))
    assert text.startswith("[0009] EVENT something_new:")


def test_render_cli_live_line_falls_back_when_rich_unavailable(monkeypatch) -> None:
    """Live line helper should preserve plain-text behavior without rich."""
    event = _event(seq=7, event="warning", data={"kind": "sample"})
    monkeypatch.setattr(cli_rich, "_RICH_AVAILABLE", False)
    line = render_cli_live_line(event, prefer_rich=True)
    assert line == render_cli_line(event)


def test_render_cli_live_line_rich_disabled_uses_plain_text() -> None:
    """Explicitly disabling rich should always keep plain-text output."""
    event = _event(seq=8, event="run_started", data={"agent_id": "agent"})
    line = render_cli_live_line(event, prefer_rich=False)
    assert line == render_cli_line(event)


class _StaticAgent:
    def __init__(self, events: list[RunStreamEvent]) -> None:
        self._events = events

    async def stream(self, run_input: AgentRunInput) -> AsyncIterator[RunStreamEvent]:
        for item in self._events:
            yield item


@pytest.mark.asyncio
async def test_cli_run_live_lines_uses_fallback_without_rich(monkeypatch) -> None:
    """Live lines should remain deterministic without optional rich dependency."""
    monkeypatch.setattr(cli_rich, "_RICH_AVAILABLE", False)
    events = [
        _event(seq=1, event="run_started", data={}),
        _event(seq=2, event="token_delta", data={"delta_text": "x"}),
    ]
    stream = _StaticAgent(events).stream(
        AgentRunInput(
            input="hello",
            run_id="run_live",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    lines = [line async for line in cli_run_live_lines(stream, prefer_rich=True)]
    assert lines[0] == render_cli_line(events[0])
    assert lines[1] == render_cli_line(events[1])
