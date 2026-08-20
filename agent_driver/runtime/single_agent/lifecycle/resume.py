"""Checkpoint resume and human-in-the-loop command handling."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast
from uuid import uuid4

from agent_driver.contracts.enums import (
    InterruptReason,
    ResumeAction,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
)
from agent_driver.context.planning.artifacts import (
    approve_plan_artifact,
    create_plan_artifact,
    mark_plan_awaiting_approval,
    plan_content_hash,
    reject_plan_artifact,
)
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.runtime.control.approval_store import (
    ApprovalConsumeRequest,
    ConsumeStatus,
)
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.errors import (
    MissingCheckpointError,
    ResumeConflictError,
    RuntimeExecutionError,
)
from agent_driver.runtime.single_agent.lifecycle.pending import (
    apply_resume_to_call,
    pending_interrupt_from_metadata,
    serialize_pending_interrupt,
)
from agent_driver.runtime.single_agent.planning.state import begin_plan_refinement
from agent_driver.runtime.single_agent.types import (
    EventSpec,
    PendingInterruptState,
    RunContext,
    RunnerDeps,
    TerminalResult,
)
from agent_driver.runtime.storage import CheckpointRecord

if TYPE_CHECKING:
    from agent_driver.context.planning.artifacts import PlanArtifactStore
    from agent_driver.execution.protocol import ExecutionBackend
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
    resume: ResumeCommand | None = None,
) -> None:
    """Store approved plan markers in run metadata and tool policy metadata."""
    payload = _plan_approval_payload(pending)
    if payload is None:
        return
    plan_id = str(
        payload.get("plan_id") or pending.interrupt.metadata.get("plan_id") or ""
    ).strip()
    # U5 — the approved plan's content hash must be HARNESS-authored, not trusted
    # from the model/tool-supplied ``content_hash``, and must reflect the content
    # actually approved. On EDIT the operator's edited plan overrides the pending
    # content; recompute the hash from whichever content was truly approved so a
    # host can detect a material revision (``detect_plan_revision``) before it
    # authorises execution.
    approved_content = str(payload.get("content") or "").strip()
    if (
        resume is not None
        and resume.action == ResumeAction.EDIT
        and resume.edited_tool_args
    ):
        edited_content = str(
            resume.edited_tool_args.get("content")
            or resume.edited_tool_args.get("plan")
            or ""
        ).strip()
        if edited_content:
            approved_content = edited_content
    if approved_content:
        content_hash = plan_content_hash(approved_content)
    else:
        content_hash = str(
            payload.get("content_hash")
            or pending.interrupt.metadata.get("content_hash")
            or ""
        ).strip()
    approved_plan: dict[str, object] = {
        "plan_id": plan_id,
        "content_hash": content_hash,
        "path": payload.get("path"),
    }
    # U5 — optional host binding: attribution + an opaque policy-snapshot the host
    # associates with THIS approved plan version. Sourced from the resume command
    # (host-authored), so model/tool output cannot forge it; survives into
    # force_planning metadata / the checkpoint below.
    if resume is not None:
        if resume.approved_by:
            approved_plan["approved_by"] = resume.approved_by
        binding = (
            resume.metadata.get("plan_policy_binding") if resume.metadata else None
        )
        if binding is not None:
            approved_plan["policy_binding"] = binding
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
    context: Any = None,
) -> dict[str, object] | None:
    payload = _plan_approval_payload(pending)
    if payload is None:
        return None
    # R3 — prefer the harness-authoritative ``approved_plan`` the approve/EDIT
    # path already computed (``_mark_force_planning_approved`` runs BEFORE this):
    # on an EDIT its ``content_hash`` is re-hashed from the edited content, so the
    # trace event must carry THAT hash, not the stale pending one.
    approved = (
        context.metadata.get("approved_plan")
        if context is not None and isinstance(getattr(context, "metadata", None), dict)
        else None
    )
    approved = approved if isinstance(approved, dict) else {}
    lifecycle: dict[str, object] = {
        "interrupt_id": resume.interrupt_id,
        "action": resume.action.value,
        "plan_id": approved.get("plan_id")
        or payload.get("plan_id")
        or pending.interrupt.metadata.get("plan_id"),
        "content_hash": approved.get("content_hash")
        or payload.get("content_hash")
        or pending.interrupt.metadata.get("content_hash"),
        "path": approved.get("path") or payload.get("path"),
    }
    # Carry the host-authored plan policy binding + approver into the
    # PLAN_APPROVED/PLAN_REJECTED trace event so a host can prove, after
    # compaction / reconnect / resume, that the executed plan is the one it
    # bound to a specific authorization/policy snapshot. Sourced from the
    # authoritative approved_plan (approve path) or the resume command (never
    # model/tool output), so the binding is unforgeable.
    approved_by = approved.get("approved_by") or resume.approved_by
    if approved_by:
        lifecycle["approved_by"] = approved_by
    binding = approved.get("policy_binding")
    if binding is None and resume.metadata:
        binding = resume.metadata.get("plan_policy_binding")
    if binding is not None:
        lifecycle["policy_binding"] = binding
    return lifecycle


def _approval_already_consumed(
    metadata: dict[str, object], resume: ResumeCommand
) -> bool:
    """True if this resume targets an interrupt a prior resume already consumed.

    Matches on the interrupt id, or on the host ``idempotency_key`` when the
    resume carries one — so a duplicate approval (HTTP-style retry) is
    recognisable even if the caller does not re-send the same interrupt id.
    """
    records = metadata.get("consumed_approvals")
    if not isinstance(records, list):
        return False
    for record in records:
        if not isinstance(record, dict):
            continue
        if record.get("interrupt_id") == resume.interrupt_id:
            return True
        if (
            resume.idempotency_key is not None
            and record.get("idempotency_key") == resume.idempotency_key
        ):
            return True
    return False


_logger = logging.getLogger(__name__)


def _persist_plan_artifact(
    store: "PlanArtifactStore",
    *,
    context: RunContext,
    pending: PendingInterruptState,
    resume: ResumeCommand,
    approved: bool,
) -> None:
    """U5 — write a durable approved/rejected PlanArtifact for a plan interrupt.

    The artifact's content hash is harness-computed from the content actually
    approved (the operator's edited plan on EDIT), so the durable record is
    hash-bound. A store write must never fail a resume — errors are swallowed.
    """
    payload = _plan_approval_payload(pending)
    if payload is None:
        return
    plan_id = str(
        payload.get("plan_id") or pending.interrupt.metadata.get("plan_id") or ""
    ).strip()
    if not plan_id:
        return
    content = str(payload.get("content") or "").strip()
    if resume.action == ResumeAction.EDIT and resume.edited_tool_args:
        edited = str(
            resume.edited_tool_args.get("content")
            or resume.edited_tool_args.get("plan")
            or ""
        ).strip()
        if edited:
            content = edited
    try:
        artifact = create_plan_artifact(
            plan_id=plan_id,
            run_id=context.run_id,
            agent_id=str(getattr(context.run_input, "agent_id", "") or "agent"),
            content=content,
            thread_id=getattr(context.run_input, "thread_id", None),
            path=(
                str(payload.get("path")) if payload.get("path") is not None else None
            ),
        )
        artifact = mark_plan_awaiting_approval(artifact)
        if approved:
            artifact = approve_plan_artifact(artifact, approved_by=resume.approved_by)
        else:
            artifact = reject_plan_artifact(
                artifact, rejected_by=resume.approved_by, reason=resume.message
            )
        store.put(artifact)
    except Exception:  # pragma: no cover - a durable write must not break a resume
        _logger.warning("plan artifact store write failed", exc_info=True)


class SingleAgentResumeMixin:  # pylint: disable=too-few-public-methods
    """Mixin: load checkpoint on resume and apply HITL resume actions."""

    _deps: RunnerDeps

    def _resolve_resume_checkpoint(
        self, run_input: AgentRunInput
    ) -> CheckpointRecord | None:
        if run_input.resume is None:
            return None
        resume = run_input.resume
        resume_token = resume.interrupt_id
        checkpoint_row = cast(
            CheckpointRecord | None,
            self._deps.checkpoint_store.load(resume_token),
        )
        latest: CheckpointRecord | None = None
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
            # Idempotent replay: the interrupt this resume targets was already
            # consumed by a prior resume (its pending state is gone). Surface a
            # stable, explicit conflict instead of the generic missing-checkpoint
            # error so the host treats a duplicate approval as a no-op rather
            # than re-executing the tool.
            if latest is not None and _approval_already_consumed(
                latest.state.metadata, resume
            ):
                raise ResumeConflictError(
                    f"interrupt '{resume_token}' was already consumed"
                )
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
        self._save_checkpoint(
            context,
            latest_output=terminal,
            node_id="resume_terminal",
        )

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
        # U3 optimistic-concurrency guard: if the host approved against a
        # specific checkpoint, refuse to apply when the pending interrupt has
        # since moved to a different checkpoint (stale approval).
        if (
            resume.expected_checkpoint_id is not None
            and resume.expected_checkpoint_id != checkpoint_row.ref.checkpoint_id
        ):
            raise ResumeConflictError(
                f"resume expected checkpoint '{resume.expected_checkpoint_id}' "
                f"but pending interrupt is at '{checkpoint_row.ref.checkpoint_id}'"
            )
        # F3 — revision-based optimistic-concurrency guard (mirrors the
        # checkpoint-id guard above, but by monotonic revision).
        if (
            resume.expected_revision is not None
            and resume.expected_revision != checkpoint_row.ref.revision
        ):
            raise ResumeConflictError(
                f"resume expected revision {resume.expected_revision} "
                f"but pending interrupt is at revision {checkpoint_row.ref.revision}"
            )
        if resume.action not in pending.interrupt.allowed_actions:
            raise RuntimeExecutionError(
                f"resume action '{resume.action.value}' is not allowed"
            )
        # U3 B/C — when a durable approval store is configured, consuming the
        # approval is an atomic compare-and-swap: exactly one concurrent client
        # wins and may drive the tool; a duplicate/conflict is refused BEFORE any
        # tool executes, closing the pre-commit race the checkpoint-based guard
        # below cannot. The row is written now (before execution), so a crash
        # between consume and result cannot let a retry run the tool twice.
        approval_store = getattr(self._deps, "approval_store", None)
        if approval_store is not None:
            outcome = approval_store.try_consume(
                ApprovalConsumeRequest(
                    run_id=context.run_id,
                    interrupt_id=pending.interrupt.interrupt_id,
                    decision=resume.action.value,
                    idempotency_key=resume.idempotency_key,
                )
            )
            if outcome.status is not ConsumeStatus.CONSUMED:
                raise ResumeConflictError(
                    f"approval for interrupt '{pending.interrupt.interrupt_id}' "
                    f"already consumed ({outcome.status.value})"
                )
        # Record the consume so a later duplicate resume (idempotency-key or
        # same interrupt id) is recognised as already-consumed rather than
        # re-driving the run. Persisted with the next checkpoint via context
        # metadata.
        consumed = list(context.metadata.get("consumed_approvals") or [])
        consumed.append(
            {
                "interrupt_id": pending.interrupt.interrupt_id,
                "idempotency_key": resume.idempotency_key,
            }
        )
        context.metadata["consumed_approvals"] = consumed
        # U5 — when a durable plan-artifact store is configured, record the
        # approved/rejected plan (hash-bound) for a plan-approval interrupt.
        plan_store = getattr(self._deps, "plan_artifact_store", None)
        if (
            plan_store is not None
            and pending.interrupt.reason == InterruptReason.PLAN_APPROVAL_REQUIRED
            and resume.action
            in {ResumeAction.APPROVE, ResumeAction.EDIT, ResumeAction.REJECT}
        ):
            _persist_plan_artifact(
                plan_store,
                context=context,
                pending=pending,
                resume=resume,
                approved=(resume.action != ResumeAction.REJECT),
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
        if resume.approved_prompts:
            approved_prompts = [
                item.model_dump(mode="json") for item in resume.approved_prompts
            ]
            context.metadata["approved_prompts"] = approved_prompts
            context.run_input = context.run_input.model_copy(
                update={
                    "app_metadata": {
                        **dict(context.run_input.app_metadata),
                        "approved_prompts": approved_prompts,
                    }
                }
            )
        if resume.message:
            context.metadata["resume_message"] = resume.message

        if resume.action == ResumeAction.CANCEL:
            self._apply_resume_cancel(context=context)
            return

        if resume.action == ResumeAction.REJECT:
            plan_payload = _plan_lifecycle_payload(
                pending, resume=resume, context=context
            )
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

        if resume.action == ResumeAction.CONTINUE:
            # A3 steering-pause resume: the run was held BEFORE the provider call by a
            # PAUSE control (MANUAL_PAUSE), not by a tool/approval. Simply continue the
            # loop from the LLM step — no approval, no tool-call replay, no budget change.
            context.metadata["next_step"] = "llm_call"
            context.metadata["pending_interrupt"] = None
            context.metadata["interrupt_payload"] = None
            context.metadata.pop("steering_pause_requested", None)
            return

        if resume.action in {ResumeAction.APPROVE, ResumeAction.EDIT}:
            _mark_force_planning_approved(context, pending=pending, resume=resume)
            plan_payload = _plan_lifecycle_payload(
                pending, resume=resume, context=context
            )
            if plan_payload is not None:
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.PLAN_APPROVED,
                        payload=plan_payload,
                    )
                )
            if (
                pending.interrupt.reason == InterruptReason.PLAN_APPROVAL_REQUIRED
                and resume.metadata.get("plan_execution_handoff") == "external"
            ):
                self._emit(
                    EventSpec(
                        run_id=context.run_id,
                        attempt_id=context.attempt_id,
                        event_type=RuntimeEventType.RUN_COMPLETED,
                        payload={
                            "finish_reason": "external_execution_handoff",
                            "terminal_reason": (
                                TerminalReason.EXTERNAL_EXECUTION_HANDOFF.value
                            ),
                        },
                    )
                )
                self._set_terminal_output(
                    context=context,
                    status=RunStatus.COMPLETED,
                    reason=TerminalReason.EXTERNAL_EXECUTION_HANDOFF,
                )
                return
            if (
                resume.action == ResumeAction.APPROVE
                and pending.interrupt.reason == InterruptReason.PLAN_APPROVAL_REQUIRED
            ):
                context.metadata["next_step"] = "llm_call"
                context.tool_calls = max(0, context.tool_calls - 1)
                context.metadata["pending_interrupt"] = None
                context.metadata["interrupt_payload"] = None
                context.metadata.pop("force_final_answer", None)
                context.metadata.pop("force_final_answer_reason", None)
                context.metadata.pop("approved_tool_call", None)
                return
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
            if pending.interrupt.reason == InterruptReason.PLAN_APPROVAL_REQUIRED:
                begin_plan_refinement(
                    context,
                    interrupt_id=pending.interrupt.interrupt_id,
                    plan_payload=_plan_approval_payload(pending),
                )

    def _init_context(
        self,
        run_input: AgentRunInput,
        *,
        abort_handle: "RunAbortHandle | None" = None,
        tool_gate: "ToolGate | None" = None,
        execution_backend: "ExecutionBackend | None" = None,
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
                    # Runner-level default budgets, so downstream checks (force-final in the
                    # tool stage) see the documented backstops instead of a hardcoded 1 when
                    # the run omits per-run budgets. app_metadata below may still override.
                    "max_steps": self._config.default_max_steps,
                    "max_tool_calls": self._config.default_max_tool_calls,
                    **(
                        run_input.app_metadata
                        if isinstance(run_input.app_metadata, dict)
                        else {}
                    ),
                },
                abort_handle=abort_handle,
                tool_gate=tool_gate,
                execution_backend=execution_backend,
            )
        metadata = dict(checkpoint_row.state.metadata)
        # F1 / U4 — a resume re-drives the run: bump the execution-attempt epoch
        # so tool results from the superseded attempt are distinguishable from
        # this one's. Only when a resume command is actually applied.
        if run_input.resume is not None:
            metadata["attempt_epoch"] = int(metadata.get("attempt_epoch") or 0) + 1
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
            execution_backend=execution_backend,
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
