"""SSE stream relay helpers for transcript persistence."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import suppress

from app.observability import start_run_span, trace_runtime_event
from app.run_cancel import clear_cancel, reset_active_run, set_active_run

from agent_driver.adapters import (
    AssistantTextCapture,
    parse_sse_data_payload,
    sse_event_stream,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.runtime.storage import RuntimeEventLog
from agent_driver.sdk import Agent

TerminalEvent = str | None
OnFinish = Callable[[str, TerminalEvent], None]
_TERMINAL_EVENTS = {
    "interrupt_requested",
    "run_completed",
    "run_failed",
    "run_cancelled",
}
_ACTIVE_RUN_TASKS: dict[str, asyncio.Task[None]] = {}


def _has_terminal_event(event_log: RuntimeEventLog, run_id: str) -> bool:
    return any(
        event.type.value in _TERMINAL_EVENTS for event in event_log.list_for_run(run_id)
    )


def ensure_run_task(
    *,
    agent: Agent,
    run_input: AgentRunInput,
    event_log: RuntimeEventLog,
    on_finish: OnFinish | None = None,
    tool_gate: ToolGate | None = None,
) -> None:
    """Start the agent run once, decoupled from any one HTTP stream."""
    run_id = run_input.run_id or ""
    if not run_id or _has_terminal_event(event_log, run_id):
        return
    existing = _ACTIVE_RUN_TASKS.get(run_id)
    if existing is not None and not existing.done():
        return
    task = asyncio.create_task(
        _drive_run(
            agent=agent,
            run_input=run_input,
            event_log=event_log,
            on_finish=on_finish,
            tool_gate=tool_gate,
        ),
        name=f"chat-run-{run_id}",
    )
    _ACTIVE_RUN_TASKS[run_id] = task

    def _discard(done: asyncio.Task[None]) -> None:
        _ACTIVE_RUN_TASKS.pop(run_id, None)
        if done.cancelled():
            return
        done.exception()

    task.add_done_callback(_discard)


async def _drive_run(
    *,
    agent: Agent,
    run_input: AgentRunInput,
    event_log: RuntimeEventLog,
    on_finish: OnFinish | None,
    tool_gate: ToolGate | None,
) -> None:
    capture = AssistantTextCapture()
    terminal_event: TerminalEvent = None
    persisted = False
    run_id = run_input.run_id or ""
    token = set_active_run(run_id or None)
    try:
        with start_run_span(run_input):
            stream = agent.stream_run(run_input, tool_gate=tool_gate)
            async for event in stream.events():
                trace_runtime_event(event.event, event.data)
                capture.apply(event_name=event.event, data=event.data)
                if event.event in _TERMINAL_EVENTS:
                    terminal_event = event.event
                    if on_finish is not None and not persisted:
                        on_finish(capture.text, terminal_event)
                        persisted = True
    finally:
        reset_active_run(token)
        if run_id:
            clear_cancel(run_id)
        if terminal_event is None and run_id:
            for event in event_log.list_for_run(run_id):
                if event.type.value in _TERMINAL_EVENTS:
                    terminal_event = event.type.value
        if on_finish is not None and not persisted:
            on_finish(capture.text, terminal_event)


async def relay_and_capture(
    *,
    agent: Agent,
    run_input: AgentRunInput,
    event_log: RuntimeEventLog,
    last_event_id: str | None,
    on_finish: OnFinish | None = None,
    keepalive_seconds: float | None = None,
) -> AsyncIterator[str]:
    """Relay SSE frames and capture finalized assistant text when present."""
    capture = AssistantTextCapture()
    terminal_event: TerminalEvent = None
    persisted = False
    run_id = run_input.run_id or ""
    token = set_active_run(run_id or None)
    try:
        with start_run_span(run_input):
            stream = sse_event_stream(
                agent=agent,
                run_input=run_input,
                event_log=event_log,
                last_event_id=last_event_id,
            ).__aiter__()
            pending: asyncio.Task[str] | None = None
            try:
                while True:
                    if pending is None:
                        pending = asyncio.create_task(anext(stream))
                    if keepalive_seconds is None or keepalive_seconds <= 0:
                        done, _pending = await asyncio.wait({pending})
                    else:
                        done, _pending = await asyncio.wait(
                            {pending},
                            timeout=keepalive_seconds,
                        )
                    if not done:
                        yield ":keepalive\n\n"
                        continue
                    try:
                        frame = pending.result()
                    except StopAsyncIteration:
                        break
                    pending = None
                    payload = parse_sse_data_payload(frame)
                    if payload is not None:
                        event_name = str(payload.get("event", ""))
                        data = payload.get("data")
                        trace_runtime_event(event_name, data)
                        capture.apply(event_name=event_name, data=data)
                        if event_name in _TERMINAL_EVENTS:
                            terminal_event = event_name
                            if on_finish is not None and not persisted:
                                on_finish(capture.text, terminal_event)
                                persisted = True
                    yield frame
            finally:
                if pending is not None and not pending.done():
                    pending.cancel()
                    with suppress(asyncio.CancelledError):
                        await pending
                aclose = getattr(stream, "aclose", None)
                if callable(aclose):
                    await aclose()
    finally:
        reset_active_run(token)
        if run_id:
            clear_cancel(run_id)
    if on_finish is not None and not persisted:
        on_finish(capture.text, terminal_event)
