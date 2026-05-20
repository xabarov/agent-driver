"""SSE stream relay helpers for transcript persistence."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
import json

from agent_driver.adapters import sse_event_stream
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.sdk import Agent

TerminalEvent = str | None
OnFinish = Callable[[str, TerminalEvent], None]
_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


def _parse_sse_payload(frame: str) -> dict[str, object] | None:
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


async def relay_and_capture(
    *,
    agent: Agent,
    run_input: AgentRunInput,
    event_log: RuntimeEventLog,
    last_event_id: str | None,
    on_finish: OnFinish | None = None,
) -> AsyncIterator[str]:
    """Relay SSE frames and capture assistant text from token deltas."""
    parts: list[str] = []
    terminal_event: TerminalEvent = None
    async for frame in sse_event_stream(
        agent=agent,
        run_input=run_input,
        event_log=event_log,
        last_event_id=last_event_id,
    ):
        payload = _parse_sse_payload(frame)
        if payload is not None:
            event_name = str(payload.get("event", ""))
            data = payload.get("data")
            if event_name == "token_delta" and isinstance(data, dict):
                delta = data.get("delta_text")
                if isinstance(delta, str) and delta:
                    parts.append(delta)
            if event_name in _TERMINAL_EVENTS:
                terminal_event = event_name
        yield frame
    if on_finish is not None:
        on_finish("".join(parts), terminal_event)

