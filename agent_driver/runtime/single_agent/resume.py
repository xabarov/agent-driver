"""Checkpoint resume and human-in-the-loop command handling."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from uuid import uuid4

from agent_driver.contracts.enums import (
    InterruptReason,
    ResumeAction,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.errors import MissingCheckpointError, RuntimeExecutionError
from agent_driver.runtime.single_agent.pending import (
    apply_resume_to_call,
    pending_interrupt_from_metadata,
    serialize_pending_interrupt,
)
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    PendingInterruptState,
    RunContext,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.runtime.storage import CheckpointRecord

if TYPE_CHECKING:
    from agent_driver.runtime.abort import RunAbortHandle
    from agent_driver.runtime.tool_gate import ToolGate


def _plan_approval_payload(pending: PendingInterruptState) -> dict[str, object] | None:
    """Return plan approval payload from a pending interrupt, if present."""
    if pending.interrupt.reason != InterruptReason.PLAN_APPROVAL_REQUIRED:
        return None
    proposed = pending.interrupt.proposed_action
    payload = proposed.get("plan_approval")
    if isinstance(payload, dict):
        return dict(payload)
    structured = pending.envelope.structured_output
    if isinstance(structured, dict):
        payload = structured.get("plan_approval")
        if isinstance(payload, dict):
            return dict(payload)
    return None


def _mark_force_planning_approved(
    context: RunContext,
    *,
    pending: PendingInterruptState,
) -> None:
    """Store approved plan markers in run metadata and tool policy metadata."""
    payload = _plan_approval_payload(pending)
    if payload is None:
        return
    plan_id = str(
        payload.get("plan_id") or pending.interrupt.metadata.get("plan_id") or ""
    ).strip()
    content_hash = str(
        payload.get("content_hash")
        or pending.interrupt.metadata.get("content_hash")
        or ""
    ).strip()
    approved_plan = {
        "plan_id": plan_id,
        "content_hash": content_hash,
        "path": payload.get("path"),
    }
    context.metadata["approved_plan"] = approved_plan
    current_policy = context.run_input.tool_policy
    policy_metadata = dict(current_policy.metadata)
    raw_force = policy_metadata.get("force_planning")
    force_planning = dict(raw_force) if isinstance(raw_force, dict) else {}
    if not force_planning and policy_metadata.get("force_planning_enabled") is True:
        force_planning["enabled"] = True
    if force_planning:
        force_planning["approved"] = True
        if plan_id:
            force_planning["approved_plan_id"] = plan_id
        force_planning["approved_plan"] = approved_plan
        policy_metadata["force_planning"] = force_planning
        context.run_input = context.run_input.model_copy(
            update={
                "tool_policy": current_policy.model_copy(
                    update={"metadata": policy_metadata}
                )
            }
        )


def _plan_lifecycle_payload(
    pending: PendingInterruptState,
    *,
    resume: ResumeCommand,
) -> dict[str, object] | None:
    payload = _plan_approval_payload(pending)
    if payload is None:
        return None
    return {
        "interrupt_id": resume.interrupt_id,
        "action": resume.action.value,
        "plan_id": payload.get("plan_id") or pending.interrupt.metadata.get("plan_id"),
        "content_hash": payload.get("content_hash")
        or pending.interrupt.metadata.get("content_hash"),
        "path": payload.get("path"),
    }


class SingleAgentResumeMixin:  # pylint: disable=too-few-public-methods
    """Mixin: load checkpoint on resume and apply HITL resume actions."""

    _deps: RunnerDeps

    def _resolve_resume_checkpoint(
        self, run_input: AgentRunInput
    ) -> CheckpointRecord | None:
        if run_input.resume is None:
            return None
        resume_token = run_input.resume.interrupt_id
        checkpoint_row = cast(
            CheckpointRecord | None,
            self._deps.checkpoint_store.load(resume_token),
        )
        if checkpoint_row is None and run_input.run_id:
            latest = cast(
                CheckpointRecord | None,
                self._deps.checkpoint_store.latest(run_input.run_id),
            )
            if latest is not None:
                pending = pending_interrupt_from_metadata(latest.state.metadata)
                if (
                    pending is not None
                    and pending.interrupt.interrupt_id == resume_token
                ):
                    checkpoint_row = latest
        if checkpoint_row is None:
            raise MissingCheckpointError(
                f"Checkpoint '{run_input.resume.interrupt_id}' not found"
            )
        return checkpoint_row

    def _set_terminal_output(
        self,
        *,
        context: RunContext,
        status: RunStatus,
        reason: TerminalReason,
    ) -> None:
        """Build and store terminal output after resume action."""
        context.metadata["interrupt_payload"] = None
        context.metadata["next_step"] = "done"
        terminal = self._build_output(
            context,
            TerminalResult(status=status, reason=reason),
        )
        context.metadata["terminal_output"] = terminal.model_dump(mode="json")
        context.metadata["pending_interrupt"] = None

    def _apply_resume_cancel(self, *, context: RunContext) -> None:
        """Apply CANCEL action for pending interrupt."""
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_CANCELLED,
                payload={"reason": TerminalReason.CANCELLED_BY_USER.value},
            )
        )
        self._set_terminal_output(
            context=context,
            status=RunStatus.CANCELLED,
            reason=TerminalReason.CANCELLED_BY_USER,
        )

    def _apply_resume_reject(self, *, context: RunContext) -> None:
        """Apply REJECT action for pending interrupt."""
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_FAILED,
                payload={"reason": TerminalReason.APPROVAL_REJECTED.value},
            )
        )
        self._set_terminal_output(
            context=context,
            status=RunStatus.FAILED,
            reason=TerminalReason.APPROVAL_REJECTED,
        )

    def _handle_resume_with_pending(
        self,
        *,
        context: RunContext,
        checkpoint_row: CheckpointRecord,
        resume: ResumeCommand,
        pending: PendingInterruptState,
    ) -> None:
        """Apply resume action for pending HITL interrupt."""
        if resume.interrupt_id not in {
            pending.interrupt.interrupt_id,
            checkpoint_row.ref.checkpoint_id,
        }:
            raise MissingCheckpointError(
                "resume interrupt_id does not match pending interrupt"
            )
        if resume.action not in pending.interrupt.allowed_actions:
            raise RuntimeExecutionError(
                f"resume action '{resume.action.value}' is not allowed"
            )
        self._emit(
            EventSpec(
                run_id=context.run_id,
                attempt_id=context.attempt_id,
                event_type=RuntimeEventType.RUN_RESUMED,
                payload={
                    "interrupt_id": resume.interrupt_id,
                    "action": resume.action.value,
                },
            )
        )
        context.metadata["resume_action"] = resume.action.value
        context.metadata["pending_interrupt"] = serialize_pending_interrupt(pending)
        if resume.message:
            context.metadata["resume_message"] = resume.message

        if resume.action == ResumeAction.CANCEL:
            self._apply_resume_cancel(context=context)
            return

        if resume.action == ResumeAction.REJECT:
            plan_payload = _plan_lifecycle_payload(pending, resume=resume)
            if plan_payload is not None:
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.PLAN_REJECTED,
                        payload=plan_payload,
                    )
                )
            self._apply_resume_reject(context=context)
            return

        if resume.action in {ResumeAction.APPROVE, ResumeAction.EDIT}:
            _mark_force_planning_approved(context, pending=pending)
            plan_payload = _plan_lifecycle_payload(pending, resume=resume)
            if plan_payload is not None:
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.PLAN_APPROVED,
                        payload=plan_payload,
                    )
                )
            call = apply_resume_to_call(
                pending.call, resume.action, resume.edited_tool_args
            )
            call = call.model_copy(
                update={
                    "metadata": {
                        **call.metadata,
                        "approved_interrupt_id": pending.interrupt.interrupt_id,
                        "resume_action": resume.action.value,
                    }
                }
            )
            context.metadata["approved_tool_call"] = call.model_dump(mode="json")
            context.metadata["next_step"] = "tool_stage"
            context.metadata["pending_interrupt"] = None
            context.metadata["interrupt_payload"] = None
            return

        if resume.action == ResumeAction.CLARIFY:
            context.metadata["next_step"] = "llm_call"
            context.metadata["pending_interrupt"] = None
            if resume.message:
                context.metadata["clarification"] = resume.message
            context.metadata["interrupt_payload"] = None

    def _init_context(
        self,
        run_input: AgentRunInput,
        *,
        abort_handle: "RunAbortHandle | None" = None,
        tool_gate: "ToolGate | None" = None,
    ) -> RunContext:
        checkpoint_row = self._resolve_resume_checkpoint(run_input)
        if checkpoint_row is None:
            run_id = run_input.run_id or f"run_{uuid4().hex}"
            return RunContext(
                run_input=run_input.model_copy(update={"run_id": run_id}),
                identifiers={
                    "run_id": run_id,
                    "attempt_id": f"attempt_{uuid4().hex[:8]}",
                },
                metadata={
                    "next_step": "run_started",
                    "step_count": 0,
                    "llm_step_count": 0,
                    "tool_calls": 0,
                    **(
                        run_input.app_metadata
                        if isinstance(run_input.app_metadata, dict)
                        else {}
                    ),
                },
                abort_handle=abort_handle,
                tool_gate=tool_gate,
            )
        metadata = dict(checkpoint_row.state.metadata)
        context = RunContext(
            run_input=run_input.model_copy(
                update={"run_id": checkpoint_row.ref.run_id}
            ),
            identifiers={
                "run_id": checkpoint_row.ref.run_id,
                "attempt_id": checkpoint_row.ref.attempt_id,
            },
            metadata=metadata,
            prior_checkpoint=checkpoint_row.ref,
            llm_response=(
                LlmResponse.model_validate(metadata["last_llm_response"])
                if isinstance(metadata.get("last_llm_response"), dict)
                else None
            ),
            abort_handle=abort_handle,
            tool_gate=tool_gate,
        )
        resume = run_input.resume
        if resume is not None:
            pending = pending_interrupt_from_metadata(metadata)
            if pending is None:
                raise RuntimeExecutionError(
                    "resume command requires pending interrupt in checkpoint metadata"
                )
            self._handle_resume_with_pending(
                context=context,
                checkpoint_row=checkpoint_row,
                resume=resume,
                pending=pending,
            )
        return context


__all__ = ["SingleAgentResumeMixin"]
