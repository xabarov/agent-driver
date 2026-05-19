"""Tests for SSE/CLI adapter rendering over stream events."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent_driver.adapters import (
    cli_replay_lines,
    cli_run_lines,
    cli_tail_lines,
    cli_tree_lines,
    parse_after_seq,
    render_cli_line,
    render_sse_line,
    sse_event_stream,
    to_sse_envelope,
)
from agent_driver.contracts import AgentRunInput, RunStreamEvent, RuntimeEventType, new_runtime_event
from agent_driver.runtime.events import InMemoryEventLog


def _sample_event() -> RunStreamEvent:
    return RunStreamEvent(
        schema_version="1.0",
        stream_id="run_1:2",
        run_id="run_1",
        attempt_id="att_1",
        seq=2,
        event="token_delta",
        source="runtime_event",
        data={"delta_text": "hi"},
        retry_ms=1500,
        runtime_event_id="evt_1",
        created_at="2026-05-19T00:00:00Z",
    )


def test_to_sse_envelope_has_event_id_and_json_data() -> None:
    """SSE envelope should include event/id/data keys."""
    envelope = to_sse_envelope(_sample_event())
    assert envelope["event"] == "token_delta"
    assert envelope["id"] == "run_1:2"
    assert envelope["retry"] == "1500"
    assert "\"delta_text\": \"hi\"" in envelope["data"]


def test_render_sse_and_cli_lines_are_deterministic() -> None:
    """SSE and CLI lines should render stable textual output."""
    event = _sample_event()
    sse = render_sse_line(event)
    cli = render_cli_line(event)
    assert "event: token_delta" in sse
    assert "id: run_1:2" in sse
    assert "retry: 1500" in sse
    assert cli.startswith("[0002] token_delta:")


def test_parse_after_seq_for_last_event_id() -> None:
    """Adapter should parse Last-Event-ID tokens into numeric after_seq."""
    assert parse_after_seq("run_1:9", run_id="run_1") == 9
    assert parse_after_seq("run_2:9", run_id="run_1") is None
    assert parse_after_seq("bad", run_id="run_1") is None


class _StaticAgent:
    def __init__(self, events: list[RunStreamEvent]) -> None:
        self._events = events

    async def stream(self, run_input: AgentRunInput) -> AsyncIterator[RunStreamEvent]:
        for event in self._events:
            yield event


@pytest.mark.asyncio
async def test_sse_stream_supports_backfill_and_live_skip_duplicates() -> None:
    """SSE stream should backfill after seq and skip duplicate live events."""
    log = InMemoryEventLog()
    log.append(
        new_runtime_event(
            event_type=RuntimeEventType.RUN_STARTED,
            context={"run_id": "run_1", "attempt_id": "att_1", "seq": 1},
        )
    )
    live = [
        RunStreamEvent(
            schema_version="1.0",
            stream_id="run_1:1",
            run_id="run_1",
            attempt_id="att_1",
            seq=1,
            event="run_started",
            source="runtime_event",
            data={},
        ),
        RunStreamEvent(
            schema_version="1.0",
            stream_id="run_1:2",
            run_id="run_1",
            attempt_id="att_1",
            seq=2,
            event="token_delta",
            source="runtime_event",
            data={"delta_text": "x"},
        ),
    ]
    lines = [
        line
        async for line in sse_event_stream(
            agent=_StaticAgent(live),
            run_input=AgentRunInput(
                input="hi",
                run_id="run_1",
                agent_id="agent",
                graph_preset="single_react",
            ),
            event_log=log,
            last_event_id="run_1:1",
        )
    ]
    assert len(lines) == 1
    assert "id: run_1:2" in lines[0]


@pytest.mark.asyncio
async def test_cli_handlers_run_replay_tail_tree() -> None:
    """CLI handlers should produce deterministic run/replay/tail/tree lines."""
    log = InMemoryEventLog()
    for seq, event_type in [
        (1, RuntimeEventType.RUN_STARTED),
        (2, RuntimeEventType.TOKEN_DELTA),
        (3, RuntimeEventType.RUN_COMPLETED),
    ]:
        log.append(
            new_runtime_event(
                event_type=event_type,
                context={"run_id": "run_cli", "attempt_id": "att_1", "seq": seq},
            )
        )
    replay = cli_replay_lines(log, run_id="run_cli")
    tail = cli_tail_lines(log, run_id="run_cli", last_n=2)
    tree = cli_tree_lines(log, run_id="run_cli")
    run_lines = [
        line
        async for line in cli_run_lines(
            _StaticAgent(
                [
                    RunStreamEvent(
                        schema_version="1.0",
                        stream_id="run_cli:4",
                        run_id="run_cli",
                        attempt_id="att_1",
                        seq=4,
                        event="warning",
                        source="runtime_event",
                        data={"kind": "sample"},
                    )
                ]
            ).stream(
                AgentRunInput(
                    input="cli",
                    run_id="run_cli",
                    agent_id="agent",
                    graph_preset="single_react",
                )
            )
        )
    ]
    assert replay[0].startswith("[0001] run_started:")
    assert len(tail) == 2
    assert any(item.startswith("run_completed:") for item in tree)
    assert run_lines == ["[0004] warning: {'kind': 'sample'}"]
