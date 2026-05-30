"""SSE adapter helpers over normalized stream events.

For projecting structured ``RuntimeEventType.WARNING`` events into a stable
shape that host applications can map to their own UI vocabulary (warning ids,
copy, suggestions), see :func:`agent_driver.adapters.project_warning_event`
and ``docs/architecture/warning-events.md``.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Protocol

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.runtime.stream import backfill_stream_events


def to_sse_envelope(event: RunStreamEvent) -> dict[str, str]:
    """Convert stream event into SSE envelope fields."""
    envelope = {
        "event": event.event,
        "id": event.stream_id,
        "data": json.dumps(event.model_dump(mode="json"), ensure_ascii=False),
    }
    if event.retry_ms is not None:
        envelope["retry"] = str(event.retry_ms)
    return envelope


def render_sse_line(event: RunStreamEvent) -> str:
    """Render one SSE frame for HTTP streaming adapters."""
    envelope = to_sse_envelope(event)
    line = (
        f"event: {envelope['event']}\n"
        f"id: {envelope['id']}\n"
        f"data: {envelope['data']}\n"
    )
    if "retry" in envelope:
        line = f"{line}retry: {envelope['retry']}\n"
    return f"{line}\n"


def parse_after_seq(last_event_id: str | None, *, run_id: str) -> int | None:
    """Decode Last-Event-ID style token into after_seq integer."""
    if not last_event_id:
        return None
    prefix = f"{run_id}:"
    if not last_event_id.startswith(prefix):
        return None
    raw = last_event_id[len(prefix) :].strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return None


def parse_sse_data_payload(frame: str) -> dict[str, object] | None:
    """Parse the JSON ``data:`` payload from one SSE frame."""
    for line in frame.splitlines():
        if not line.startswith("data: "):
            continue
        try:
            payload = json.loads(line[6:])
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None
    return None


class AssistantTextCapture:
    """Capture finalized assistant text from normalized stream events."""

    def __init__(self) -> None:
        self._parts: list[str] = []

    @property
    def text(self) -> str:
        """Return current captured assistant text."""
        return "".join(self._parts)

    def apply(self, *, event_name: str, data: object) -> None:
        """Update captured text from one stream event."""
        if not isinstance(data, dict):
            return
        if event_name == "token_delta":
            delta = data.get("delta_text")
            if isinstance(delta, str) and delta:
                self._parts.append(delta)
            return
        if event_name in {"assistant_message_completed", "assistant_message_replaced"}:
            content = data.get("content")
            if isinstance(content, str):
                self._parts[:] = [content]
            return
        if event_name == "assistant_message_tombstoned":
            self._parts.clear()


class StreamAgent(Protocol):
    """Protocol for SDK agent stream method used by adapters."""

    async def stream(self, run_input: AgentRunInput) -> AsyncIterator[RunStreamEvent]:
        """Yield stream events for one run input."""


async def sse_event_stream(
    *,
    agent: StreamAgent,
    run_input: AgentRunInput,
    event_log: RuntimeEventLog | None = None,
    last_event_id: str | None = None,
) -> AsyncIterator[str]:
    """Yield rendered SSE frames with optional reconnect backfill."""
    after_seq = parse_after_seq(last_event_id, run_id=run_input.run_id or "")
    if event_log is not None and run_input.run_id and after_seq is not None:
        for event in backfill_stream_events(
            event_log, run_id=run_input.run_id, after_seq=after_seq
        ):
            yield render_sse_line(event)
    live_after_seq = after_seq or 0
    async for event in agent.stream(run_input):
        if event.seq <= live_after_seq:
            continue
        yield render_sse_line(event)


__all__ = [
    "AssistantTextCapture",
    "parse_after_seq",
    "parse_sse_data_payload",
    "render_sse_line",
    "sse_event_stream",
    "to_sse_envelope",
]
