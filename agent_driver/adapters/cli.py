"""CLI adapter baseline for deterministic stream rendering."""

from __future__ import annotations

from collections import Counter
from collections.abc import AsyncIterator

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.runtime.stream import backfill_stream_events


def render_cli_line(event: RunStreamEvent) -> str:
    """Render deterministic plain-text line for stream event."""
    return f"[{event.seq:04d}] {event.event}: {event.data}"


async def cli_run_lines(
    stream: AsyncIterator[RunStreamEvent],
) -> AsyncIterator[str]:
    """Yield deterministic CLI lines for live stream consumption."""
    async for event in stream:
        yield render_cli_line(event)


def cli_replay_lines(
    event_log: RuntimeEventLog, *, run_id: str, after_seq: int | None = None
) -> list[str]:
    """Render replay lines from persisted event log."""
    return [
        render_cli_line(event)
        for event in backfill_stream_events(event_log, run_id=run_id, after_seq=after_seq)
    ]


def cli_tail_lines(
    event_log: RuntimeEventLog, *, run_id: str, last_n: int = 20
) -> list[str]:
    """Render last N stream lines for one run."""
    lines = cli_replay_lines(event_log, run_id=run_id)
    if last_n <= 0:
        return []
    return lines[-last_n:]


def cli_tree_lines(event_log: RuntimeEventLog, *, run_id: str) -> list[str]:
    """Render compact event-kind counts for one run."""
    events = backfill_stream_events(event_log, run_id=run_id)
    counts = Counter(event.event for event in events)
    return [f"{name}: {counts[name]}" for name in sorted(counts)]


__all__ = [
    "cli_replay_lines",
    "cli_run_lines",
    "cli_tail_lines",
    "cli_tree_lines",
    "render_cli_line",
]
