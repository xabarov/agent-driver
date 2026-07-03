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
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.enums import ResumeAction, RunStatus
from agent_driver.contracts.durable_lifecycle import (
    BackgroundRunLease,
    DurableAbortRequestRecord,
    DurableApprovalRecord,
    DurableApprovalStatus,
    DurableDurabilityLevel,
    DurableInterruptRecord,
    DurableInterruptStatus,
    DurableLeaseStatus,
    DurableLifecycleStatus,
    DurableRunRecord,
    DurableSessionRecord,
)
from agent_driver.contracts.harness_adapter import (
    HarnessAdapterCapability,
    HarnessAdapterEvent,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.stream import summarize_run_lifecycle
from agent_driver.harness import (
    DurableLifecycleRepository,
    build_harness_adapter_capability,
    project_harness_adapter_events,
)
from agent_driver.server.usage import chat_usage

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


def harness_adapter_events_for_server_run(
    record: "RunRecord",
    *,
    source: str = "replay",
) -> list[HarnessAdapterEvent]:
    """Project HTTP server run events through the shared harness contract."""
    return project_harness_adapter_events(
        _server_stream_events(record),
        session_id=record.thread_id,
        source=source,
    )


def server_harness_adapter_capability() -> HarnessAdapterCapability:
    """Return the OpenAI-compatible server's shared harness capability manifest."""
    return build_harness_adapter_capability(
        adapter_id="openai_server",
        product_family="generic_protocol",
        protocol="openai_compatible_http",
        durability_level="process_local",
        features={
            "streaming": "supported",
            "replay": "supported",
            "cursor_reconnect": "supported",
            "approvals": "supported",
            "interrupts": "supported",
            "artifacts": "no_claim",
            "support_bundles": "no_claim",
            "fork": "unsupported",
            "background_logs": "unsupported",
            "ui_projection": "no_claim",
            "live_gates": "no_claim",
        },
        scenario_ids=["harness_adapter.openai_server.basic_run.v1"],
    )


@dataclass
class _Subscriber:
    queue: "asyncio.Queue[dict[str, Any] | None]"


@dataclass
class RunRecord:
    """In-memory state for one async run."""

    run_id: str
    created: int
    thread_id: str | None = None
    status: str = QUEUED
    answer: str | None = None
    error: str | None = None
    usage: dict[str, int] | None = None
    interrupt: dict[str, Any] | None = None
    abort: RunAbortHandle = field(default_factory=RunAbortHandle)
    events: list[dict[str, Any]] = field(default_factory=list)
    subscribers: list[_Subscriber] = field(default_factory=list)
    # Resolved by an approval call to unblock the parked drive loop.
    approval: "asyncio.Future[tuple[ResumeAction, str | None, dict[str, Any] | None]] | None" = None
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
        body["lifecycle"] = self.lifecycle_snapshot()
        return body

    def lifecycle_snapshot(self) -> dict[str, Any]:
        """Canonical lifecycle snapshot derived from server-local state."""
        active = self.task is not None and not self.task.done()
        snapshot = summarize_run_lifecycle(
            _server_stream_events(self),
            active_task=active,
            abort_requested=self.abort.is_aborted and self.status not in _TERMINAL,
            abort_reason=self.abort.reason,
            paused_interrupt_id=(
                str(self.interrupt.get("interrupt_id"))
                if isinstance(self.interrupt, dict)
                and self.interrupt.get("interrupt_id")
                else None
            ),
            resume_available=self.status == REQUIRES_ACTION,
            durability="server_memory",
            adapter_id="agent_driver.server.runs",
            session_id=self.thread_id,
            thread_id=self.thread_id,
        )
        return snapshot.model_dump(mode="json")


class RunManager:
    """Owns the async runs for one server: start / get / events / approve / stop."""

    def __init__(
        self,
        agent: "Agent",
        *,
        max_runs: int = 1024,
        durable_lifecycle_writer: DurableLifecycleRepository | None = None,
    ) -> None:
        self._agent = agent
        self._runs: dict[str, RunRecord] = {}
        self._max_runs = max(1, max_runs)
        self._durable_lifecycle_writer = durable_lifecycle_writer

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
        record = RunRecord(run_id=run_id, created=int(time.time()), thread_id=thread_id)
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
        self._durable_start(record)
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
        self._durable_approval_resolved(record, action, message, edited_tool_args)
        future.set_result((action, message, edited_tool_args))
        return True

    def stop(self, run_id: str) -> bool:
        """Request cancellation of a run; returns False if unknown/terminal."""
        record = self._runs.get(run_id)
        if record is None or record.status in _TERMINAL:
            return False
        record.abort.abort(reason="runs_stop")
        self._durable_stop(record)
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
        payload = {
            "event": event,
            "seq": len(record.events) + 1,
            "data": {"run_id": record.run_id, **data},
        }
        record.events.append(payload)
        self._durable_emit(record, event, payload)
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
        self._durable_approval_requested(record, dict(record.interrupt))
        decision = await record.approval
        record.approval = None
        record.interrupt = None
        record.status = RUNNING
        return decision

    def _finalize(self, record: RunRecord, output: Any) -> None:
        status = getattr(output.status, "value", output.status)
        if output.usage is not None:
            record.usage = chat_usage(output)
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

    def _durable_start(self, record: RunRecord) -> None:
        writer = self._durable_lifecycle_writer
        if writer is None:
            return
        session_id = record.thread_id or f"{record.run_id}:session"
        lease_id = f"{record.run_id}:server-process"
        writer.upsert_session(
            DurableSessionRecord(
                session_id=session_id,
                adapter_id="openai_server",
                current_run_id=record.run_id,
                lifecycle_state=DurableLifecycleStatus.QUEUED,
                created_at=str(record.created),
                updated_at=str(record.created),
                durability_level=DurableDurabilityLevel.PROCESS_LOCAL,
                search_metadata={"server_run": record.run_id},
            )
        )
        writer.upsert_run(
            DurableRunRecord(
                run_id=record.run_id,
                session_id=session_id,
                status=DurableLifecycleStatus.QUEUED,
                active_lease_id=lease_id,
                durability_level=DurableDurabilityLevel.PROCESS_LOCAL,
                redacted_metadata={"source": "server_run_manager"},
            )
        )
        writer.upsert_lease(
            BackgroundRunLease(
                lease_id=lease_id,
                run_id=record.run_id,
                owner_process_id=str(os.getpid()),
                owner_host_id="agent_driver.server",
                status=DurableLeaseStatus.ACTIVE,
                takeover_policy="manual",
            )
        )

    def _durable_emit(
        self, record: RunRecord, event_name: str, payload: dict[str, Any]
    ) -> None:
        writer = self._durable_lifecycle_writer
        if writer is None:
            return
        seq = payload["seq"]
        data = payload.get("data")
        stream_event = RunStreamEvent(
            stream_id=f"{record.run_id}:{seq}",
            run_id=record.run_id,
            attempt_id="server_attempt",
            seq=seq,
            event=event_name.replace(".", "_"),
            source="server_run_record",
            data=dict(data) if isinstance(data, dict) else {},
        )
        writer.append_event(stream_event)
        run = writer.get_run(record.run_id)
        if run is None:
            return
        status = _durable_status_for_server_event(event_name)
        updates: dict[str, Any] = {
            "status": status,
            "latest_seq": seq,
            "reconnect_cursor": f"{record.run_id}:{seq}",
        }
        if status == DurableLifecycleStatus.PAUSED and record.interrupt:
            updates["paused_interrupt_id"] = str(record.interrupt.get("interrupt_id"))
        if status in {
            DurableLifecycleStatus.COMPLETED,
            DurableLifecycleStatus.FAILED,
            DurableLifecycleStatus.CANCELLED,
        }:
            updates["active_lease_id"] = None
            updates["terminal_verdict"] = status.value
            lease = writer.leases.get(f"{record.run_id}:server-process")
            if lease is not None:
                writer.upsert_lease(
                    lease.model_copy(update={"status": DurableLeaseStatus.RELEASED})
                )
        writer.upsert_run(run.model_copy(update=updates))

    def _durable_approval_requested(
        self, record: RunRecord, interrupt: dict[str, Any]
    ) -> None:
        writer = self._durable_lifecycle_writer
        if writer is None:
            return
        interrupt_id = str(
            interrupt.get("interrupt_id") or f"{record.run_id}:interrupt"
        )
        writer.upsert_interrupt(
            DurableInterruptRecord(
                interrupt_id=interrupt_id,
                run_id=record.run_id,
                status=DurableInterruptStatus.PENDING,
                reason=str(interrupt.get("reason") or "approval"),
                allowed_actions=[
                    str(action) for action in interrupt.get("allowed_actions", [])
                ],
                approval_payload_summary={
                    "title": interrupt.get("title"),
                    "description": interrupt.get("description"),
                },
                created_at=str(int(time.time())),
            )
        )
        writer.upsert_approval(
            DurableApprovalRecord(
                approval_id=f"{interrupt_id}:approval",
                interrupt_id=interrupt_id,
                run_id=record.run_id,
                status=DurableApprovalStatus.PENDING,
                request_summary={
                    "allowed_actions": interrupt.get("allowed_actions", [])
                },
                requested_at=str(int(time.time())),
            )
        )

    def _durable_approval_resolved(
        self,
        record: RunRecord,
        action: ResumeAction,
        message: str | None,
        edited_tool_args: dict[str, Any] | None,
    ) -> None:
        writer = self._durable_lifecycle_writer
        if writer is None or record.interrupt is None:
            return
        interrupt_id = str(record.interrupt.get("interrupt_id"))
        interrupt = writer.interrupts.get(interrupt_id)
        if interrupt is not None:
            writer.upsert_interrupt(
                interrupt.model_copy(
                    update={
                        "status": DurableInterruptStatus.RESOLVED,
                        "resolution": {"action": action.value, "message": message},
                        "resolved_at": str(int(time.time())),
                    }
                )
            )
        approval = next(
            (
                item
                for item in writer.approvals.values()
                if item.interrupt_id == interrupt_id
            ),
            None,
        )
        if approval is not None:
            writer.upsert_approval(
                approval.model_copy(
                    update={
                        "status": _durable_approval_status(action),
                        "response_action": action.value,
                        "response_summary": {
                            "message": message,
                            "edited_tool_args": bool(edited_tool_args),
                        },
                        "resolved_at": str(int(time.time())),
                    }
                )
            )

    def _durable_stop(self, record: RunRecord) -> None:
        writer = self._durable_lifecycle_writer
        if writer is None:
            return
        abort_id = f"{record.run_id}:runs_stop"
        run = writer.get_run(record.run_id)
        if run is not None:
            writer.upsert_run(run.model_copy(update={"abort_request_id": abort_id}))
        writer.upsert_abort(
            DurableAbortRequestRecord(
                abort_request_id=abort_id,
                run_id=record.run_id,
                reason="runs_stop",
                requested_at=str(int(time.time())),
                requested_by="server",
            )
        )


def _server_stream_events(record: RunRecord) -> list[RunStreamEvent]:
    events: list[RunStreamEvent] = []
    for index, event in enumerate(record.events, start=1):
        data = event.get("data")
        event_name = str(event.get("event") or "").replace(".", "_")
        if not event_name:
            continue
        seq = event.get("seq")
        events.append(
            RunStreamEvent(
                stream_id=f"{record.run_id}:{seq if isinstance(seq, int) else index}",
                run_id=record.run_id,
                attempt_id="server_attempt",
                seq=seq if isinstance(seq, int) else index,
                event=event_name,
                source="server_run_record",
                data=dict(data) if isinstance(data, dict) else {},
            )
        )
    return events


def _durable_status_for_server_event(event_name: str) -> DurableLifecycleStatus:
    return {
        "run.started": DurableLifecycleStatus.ACTIVE,
        "run.requires_action": DurableLifecycleStatus.PAUSED,
        "run.completed": DurableLifecycleStatus.COMPLETED,
        "run.failed": DurableLifecycleStatus.FAILED,
        "run.cancelled": DurableLifecycleStatus.CANCELLED,
    }.get(event_name, DurableLifecycleStatus.ACTIVE)


def _durable_approval_status(action: ResumeAction) -> DurableApprovalStatus:
    return {
        ResumeAction.APPROVE: DurableApprovalStatus.APPROVED,
        ResumeAction.REJECT: DurableApprovalStatus.REJECTED,
        ResumeAction.EDIT: DurableApprovalStatus.EDITED,
        ResumeAction.CLARIFY: DurableApprovalStatus.CLARIFY,
        ResumeAction.CANCEL: DurableApprovalStatus.CANCELLED,
    }[action]


__all__ = [
    "RunManager",
    "RunRecord",
    "harness_adapter_events_for_server_run",
    "resume_action_for",
    "server_harness_adapter_capability",
]
