"""Async run lifecycle for the HTTP server (``/v1/runs``).

Brings long-running + human-in-the-loop to HTTP: a run executes in the
background, its status is pollable, its lifecycle events stream over SSE, and an
approval interrupt parks the run until a client resolves it (or stops it). This
is the HTTP analog of the ACP permission round-trip and the in-process
:class:`AgentGateway`, but with the run owned by a background task so the
``POST`` returns immediately.

No business logic lives here beyond the lifecycle bookkeeping — the run is driven
through ``agent.run`` / ``agent.resume`` (the same path the rest of the SDK uses)
and parked on ``RunStatus.PAUSED`` interrupts.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.runtime.abort import RunAbortHandle

if TYPE_CHECKING:
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.sdk.agent import Agent

# run.status values surfaced to clients.
QUEUED = "queued"
RUNNING = "running"
REQUIRES_ACTION = "requires_action"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"

_TERMINAL = {COMPLETED, FAILED, CANCELLED}

# OpenAI-ish action ids accepted by the approval endpoint -> ResumeAction.
_ACTION_MAP = {
    "approve": ResumeAction.APPROVE,
    "allow": ResumeAction.APPROVE,
    "reject": ResumeAction.REJECT,
    "deny": ResumeAction.REJECT,
    "cancel": ResumeAction.CANCEL,
    "edit": ResumeAction.EDIT,
    "clarify": ResumeAction.CLARIFY,
}


def resume_action_for(action: str) -> ResumeAction | None:
    """Map a client approval action id to a runtime ``ResumeAction``."""
    return _ACTION_MAP.get((action or "").strip().lower())


@dataclass
class _Subscriber:
    queue: "asyncio.Queue[dict[str, Any] | None]"


@dataclass
class RunRecord:
    """In-memory state for one async run."""

    run_id: str
    created: int
    status: str = QUEUED
    answer: str | None = None
    error: str | None = None
    usage: dict[str, int] | None = None
    interrupt: dict[str, Any] | None = None
    abort: RunAbortHandle = field(default_factory=RunAbortHandle)
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[_Subscriber] = field(default_factory=list)
    # Resolved by an approval call to unblock the parked drive loop.
    approval: (
        "asyncio.Future[tuple[ResumeAction, str | None, dict[str, Any] | None]] | None"
    ) = None
    task: "asyncio.Task[None] | None" = None

    def public(self) -> dict[str, Any]:
        """The pollable run object (GET /v1/runs/{id})."""
        body: dict[str, Any] = {
            "id": self.run_id,
            "object": "run",
            "created": self.created,
            "status": self.status,
        }
        if self.answer is not None:
            body["answer"] = self.answer
        if self.interrupt is not None:
            body["required_action"] = self.interrupt
        if self.usage is not None:
            body["usage"] = self.usage
        if self.error is not None:
            body["error"] = {"message": self.error}
        return body


class RunManager:
    """Owns the async runs for one server: start / get / events / approve / stop."""

    def __init__(self, agent: "Agent", *, max_runs: int = 1024) -> None:
        self._agent = agent
        self._runs: dict[str, RunRecord] = {}
        self._max_runs = max(1, max_runs)

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        messages: list["ChatMessage"],
        *,
        thread_id: str | None = None,
        model: str | None = None,
    ) -> RunRecord:
        """Create a run record and spawn its background drive task."""
        run_id = f"run_{uuid.uuid4().hex[:12]}"
        record = RunRecord(run_id=run_id, created=int(time.time()))
        self._runs[run_id] = record
        self._evict()
        run_input = AgentRunInput(
            messages=messages,
            run_id=run_id,
            thread_id=thread_id,
            agent_id=self._agent.defaults.agent_id,
            graph_preset=self._agent.defaults.graph_preset,
            app_metadata={"openai_model": model} if model else {},
        )
        record.task = asyncio.create_task(self._drive(record, run_input))
        return record

    def get(self, run_id: str) -> RunRecord | None:
        return self._runs.get(run_id)

    async def approve(
        self,
        run_id: str,
        action: ResumeAction,
        *,
        message: str | None = None,
        edited_tool_args: dict[str, Any] | None = None,
    ) -> bool:
        """Resolve a parked run's approval; returns False if not awaiting one."""
        record = self._runs.get(run_id)
        if record is None or record.status != REQUIRES_ACTION:
            return False
        future = record.approval
        if future is None or future.done():
            return False
        future.set_result((action, message, edited_tool_args))
        return True

    def stop(self, run_id: str) -> bool:
        """Request cancellation of a run; returns False if unknown/terminal."""
        record = self._runs.get(run_id)
        if record is None or record.status in _TERMINAL:
            return False
        record.abort.abort(reason="runs_stop")
        # Unblock a parked approval so the drive loop can observe the cancel.
        if record.approval is not None and not record.approval.done():
            record.approval.set_result((ResumeAction.CANCEL, None, None))
        return True

    async def stream_events(self, run_id: str):
        """Yield this run's lifecycle events (history first, then live)."""
        record = self._runs.get(run_id)
        if record is None:
            return
        queue: "asyncio.Queue[dict[str, Any] | None]" = asyncio.Queue()
        for event in list(record.events):
            queue.put_nowait(event)
        if record.status in _TERMINAL:
            queue.put_nowait(None)  # sentinel: stream ends
        else:
            record.subscribers.append(_Subscriber(queue))
        while True:
            event = await queue.get()
            if event is None:
                break
            yield event

    # -- internals ---------------------------------------------------------

    def _emit(self, record: RunRecord, event: str, data: dict[str, Any]) -> None:
        payload = {"event": event, "data": {"run_id": record.run_id, **data}}
        record.events.append(payload)
        terminal = event.split(".", 1)[-1] in _TERMINAL or event == "run.completed"
        for sub in record.subscribers:
            sub.queue.put_nowait(payload)
            if terminal:
                sub.queue.put_nowait(None)

    async def _drive(self, record: RunRecord, run_input: AgentRunInput) -> None:
        record.status = RUNNING
        self._emit(record, "run.started", {})
        try:
            output = await self._agent.run(run_input, abort_handle=record.abort)
            while (
                output.status == RunStatus.PAUSED
                and output.interrupt is not None
                and not record.abort.is_aborted
            ):
                action, message, edited = await self._await_approval(record, output)
                output = await self._agent.resume(
                    run_id=output.run_id,
                    interrupt_id=output.interrupt.interrupt_id,
                    action=action,
                    message=message,
                    edited_tool_args=edited,
                )
        except Exception as exc:  # noqa: BLE001 - surfaced as run.failed
            record.status = FAILED
            record.error = f"{type(exc).__name__}: {exc}"
            self._emit(record, "run.failed", {"error": record.error})
            return
        self._finalize(record, output)

    async def _await_approval(
        self, record: RunRecord, output: Any
    ) -> tuple[ResumeAction, str | None, dict[str, Any] | None]:
        interrupt = output.interrupt
        record.interrupt = {
            "interrupt_id": interrupt.interrupt_id,
            "reason": getattr(interrupt.reason, "value", str(interrupt.reason)),
            "title": interrupt.title,
            "description": interrupt.description,
            "allowed_actions": [a.value for a in interrupt.allowed_actions],
        }
        record.status = REQUIRES_ACTION
        record.approval = asyncio.get_running_loop().create_future()
        self._emit(record, "run.requires_action", dict(record.interrupt))
        decision = await record.approval
        record.approval = None
        record.interrupt = None
        record.status = RUNNING
        return decision

    def _finalize(self, record: RunRecord, output: Any) -> None:
        status = getattr(output.status, "value", output.status)
        usage = output.usage
        if usage is not None:
            record.usage = {
                "prompt_tokens": int(getattr(usage, "input_tokens", 0) or 0),
                "completion_tokens": int(getattr(usage, "output_tokens", 0) or 0),
                "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
            }
        if status == "completed":
            record.status = COMPLETED
            record.answer = output.answer or ""
            self._emit(
                record,
                "run.completed",
                {"answer": record.answer, "usage": record.usage},
            )
        elif status == "cancelled":
            record.status = CANCELLED
            self._emit(record, "run.cancelled", {})
        else:
            record.status = FAILED
            reason = getattr(output.terminal_reason, "value", output.terminal_reason)
            record.error = str(reason or status)
            self._emit(record, "run.failed", {"error": record.error})

    def _evict(self) -> None:
        """Bound memory: drop the oldest terminal runs past the cap."""
        if len(self._runs) <= self._max_runs:
            return
        terminal = [rid for rid, r in self._runs.items() if r.status in _TERMINAL]
        for rid in terminal[: len(self._runs) - self._max_runs]:
            self._runs.pop(rid, None)


__all__ = ["RunManager", "RunRecord", "resume_action_for"]
