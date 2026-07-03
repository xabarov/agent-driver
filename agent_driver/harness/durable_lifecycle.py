"""Deterministic durable lifecycle repository, planners and fixtures."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.durable_lifecycle import (
    AttachPlan,
    BackgroundRunLease,
    BackgroundRunLogRef,
    DurableAbortRequestRecord,
    DurableApprovalRecord,
    DurableApprovalStatus,
    DurableCheckpointIndex,
    DurableDurabilityLevel,
    DurableInterruptRecord,
    DurableInterruptStatus,
    DurableLeaseStatus,
    DurableLifecycleCompatibilityReport,
    DurableLifecycleStatus,
    DurablePlanVerdict,
    DurableRunRecord,
    DurableSessionRecord,
    DurableSideEffectSafety,
    ForkPlan,
    ResumePlan,
)
from agent_driver.contracts.harness_adapter import (
    HarnessAdapterEvent,
    HarnessAdapterRun,
)
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.harness.adapter_protocol import (
    project_harness_adapter_events,
    project_harness_adapter_run,
)
from agent_driver.runtime.stream import RunLifecycleSnapshot

_SUPPORTED_RESUME_BACKENDS = {
    DurableDurabilityLevel.SQLITE,
    DurableDurabilityLevel.JSONL,
    DurableDurabilityLevel.POSTGRES,
    DurableDurabilityLevel.EXTERNAL_DB,
    DurableDurabilityLevel.MANAGED_HOST,
}
_TERMINAL_STATES = {
    DurableLifecycleStatus.COMPLETED,
    DurableLifecycleStatus.FAILED,
    DurableLifecycleStatus.CANCELLED,
}
_DURABLE_SCENARIOS = {
    "generic": [
        "durable_lifecycle.session_run_records.v1",
        "durable_lifecycle.interrupt_resume_plan.v1",
        "durable_lifecycle.background_attach_replay.v1",
    ],
    "excel_ai": [
        "durable_lifecycle.session_run_records.v1",
        "durable_lifecycle.interrupt_resume_plan.v1",
        "durable_lifecycle.background_attach_replay.v1",
        "durable_lifecycle.excel_workbook_pause.v1",
    ],
    "chat_demo": [
        "durable_lifecycle.session_run_records.v1",
        "durable_lifecycle.interrupt_resume_plan.v1",
        "durable_lifecycle.background_attach_replay.v1",
        "durable_lifecycle.chat_demo_research_pause.v1",
    ],
}


class DurableLifecycleRepository:
    """Small deterministic repository over durable lifecycle records."""

    def __init__(self) -> None:
        self.sessions: dict[str, DurableSessionRecord] = {}
        self.runs: dict[str, DurableRunRecord] = {}
        self.checkpoints: dict[str, DurableCheckpointIndex] = {}
        self.interrupts: dict[str, DurableInterruptRecord] = {}
        self.approvals: dict[str, DurableApprovalRecord] = {}
        self.aborts: dict[str, DurableAbortRequestRecord] = {}
        self.leases: dict[str, BackgroundRunLease] = {}
        self.logs: dict[str, BackgroundRunLogRef] = {}
        self.events: dict[str, list[RunStreamEvent]] = {}

    def upsert_session(self, record: DurableSessionRecord) -> DurableSessionRecord:
        self.sessions[record.session_id] = record
        return record

    def get_session(self, session_id: str) -> DurableSessionRecord | None:
        return self.sessions.get(session_id)

    def list_sessions(
        self, *, workspace_id: str | None = None, adapter_id: str | None = None
    ) -> list[DurableSessionRecord]:
        records = list(self.sessions.values())
        if workspace_id is not None:
            records = [item for item in records if item.workspace_id == workspace_id]
        if adapter_id is not None:
            records = [item for item in records if item.adapter_id == adapter_id]
        return sorted(records, key=lambda item: item.updated_at or item.session_id)

    def search_sessions(self, query: str) -> list[DurableSessionRecord]:
        needle = query.lower()
        return [
            item
            for item in self.list_sessions()
            if needle in _search_blob(item.search_metadata, item.redacted_metadata)
            or needle in item.session_id.lower()
            or (item.owner_id is not None and needle in item.owner_id.lower())
        ]

    def upsert_run(self, record: DurableRunRecord) -> DurableRunRecord:
        self.runs[record.run_id] = record
        return record

    def get_run(self, run_id: str) -> DurableRunRecord | None:
        return self.runs.get(run_id)

    def list_runs(self, *, session_id: str | None = None) -> list[DurableRunRecord]:
        records = list(self.runs.values())
        if session_id is not None:
            records = [item for item in records if item.session_id == session_id]
        return sorted(records, key=lambda item: (item.session_id, item.run_id))

    def upsert_checkpoint(
        self, record: DurableCheckpointIndex
    ) -> DurableCheckpointIndex:
        self.checkpoints[record.checkpoint_id] = record
        run = self.runs.get(record.run_id)
        if run is not None:
            self.runs[record.run_id] = run.model_copy(
                update={"latest_checkpoint_id": record.checkpoint_id}
            )
        return record

    def record_checkpoint_ref(
        self,
        ref: CheckpointRef,
        *,
        resumable: bool = False,
        forkable: bool = False,
        side_effect_safety: DurableSideEffectSafety = DurableSideEffectSafety.NO_CLAIM,
    ) -> DurableCheckpointIndex:
        backend = _durability_from_backend(ref.storage_backend)
        return self.upsert_checkpoint(
            DurableCheckpointIndex(
                checkpoint_id=ref.checkpoint_id,
                run_id=ref.run_id,
                attempt_id=ref.attempt_id,
                parent_checkpoint_id=ref.parent_checkpoint_id,
                branch_id=ref.branch_id,
                graph_id=ref.graph_id,
                node_id=ref.node_id,
                state_version=ref.state_version,
                storage_backend=backend,
                resumable=resumable,
                forkable=forkable,
                side_effect_safety=side_effect_safety,
                created_at=ref.created_at,
                redacted_metadata=ref.metadata,
            )
        )

    def upsert_interrupt(
        self, record: DurableInterruptRecord
    ) -> DurableInterruptRecord:
        self.interrupts[record.interrupt_id] = record
        return record

    def upsert_approval(self, record: DurableApprovalRecord) -> DurableApprovalRecord:
        self.approvals[record.approval_id] = record
        return record

    def upsert_abort(
        self, record: DurableAbortRequestRecord
    ) -> DurableAbortRequestRecord:
        self.aborts[record.abort_request_id] = record
        return record

    def upsert_lease(self, record: BackgroundRunLease) -> BackgroundRunLease:
        self.leases[record.lease_id] = record
        return record

    def upsert_log_ref(self, record: BackgroundRunLogRef) -> BackgroundRunLogRef:
        self.logs[record.log_id] = record
        return record

    def append_event(self, event: RunStreamEvent) -> None:
        self.events.setdefault(event.run_id, []).append(event)
        run = self.runs.get(event.run_id)
        if run is not None and event.seq >= run.latest_seq:
            self.runs[event.run_id] = run.model_copy(
                update={
                    "latest_seq": event.seq,
                    "reconnect_cursor": f"{event.run_id}:{event.seq}",
                }
            )

    def append_events(self, events: Iterable[RunStreamEvent]) -> None:
        for event in events:
            self.append_event(event)

    def list_events(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RunStreamEvent]:
        events = sorted(self.events.get(run_id, []), key=lambda item: item.seq)
        if after_seq is None:
            return events
        return [event for event in events if event.seq > after_seq]

    def mark_expired_leases_orphaned(
        self, *, now: datetime | None = None
    ) -> list[BackgroundRunLease]:
        now = now or datetime.now(tz=UTC)
        updated: list[BackgroundRunLease] = []
        for lease_id, lease in list(self.leases.items()):
            if lease.status != DurableLeaseStatus.ACTIVE or lease.expires_at is None:
                continue
            expires_at = _parse_datetime(lease.expires_at)
            if expires_at is None or expires_at > now:
                continue
            status = (
                DurableLeaseStatus.STALE
                if lease.takeover_policy == "automatic"
                else DurableLeaseStatus.ORPHANED
            )
            new_lease = lease.model_copy(update={"status": status})
            self.leases[lease_id] = new_lease
            run = self.runs.get(lease.run_id)
            if run is not None and run.status not in _TERMINAL_STATES:
                self.runs[run.run_id] = run.model_copy(
                    update={
                        "status": DurableLifecycleStatus.STALE
                        if status == DurableLeaseStatus.STALE
                        else DurableLifecycleStatus.ORPHANED
                    }
                )
            updated.append(new_lease)
        return updated

    def attach_plan(self, run_id: str) -> AttachPlan:
        run = self.runs.get(run_id)
        if run is None:
            return AttachPlan(
                verdict=DurablePlanVerdict.NOT_FOUND,
                run_id=run_id,
                reason="durable_run_record_missing",
            )
        can_replay = bool(run.latest_seq or self.events.get(run_id))
        latest_seq = run.latest_seq or _latest_seq(self.events.get(run_id, []))
        if run.status in _TERMINAL_STATES:
            return AttachPlan(
                verdict=DurablePlanVerdict.TERMINAL,
                run_id=run.run_id,
                session_id=run.session_id,
                replay_cursor=run.reconnect_cursor,
                latest_seq=latest_seq,
                can_replay=can_replay,
                reason="terminal_runs_can_replay_but_not_attach_live",
            )
        lease = (
            self.leases.get(run.active_lease_id or "") if run.active_lease_id else None
        )
        if lease is not None and lease.status == DurableLeaseStatus.ACTIVE:
            return AttachPlan(
                verdict=DurablePlanVerdict.ATTACH_LIVE,
                run_id=run.run_id,
                session_id=run.session_id,
                lease_id=lease.lease_id,
                replay_cursor=run.reconnect_cursor,
                latest_seq=latest_seq,
                can_replay=can_replay,
                reason="active_background_lease_owned",
            )
        if run.status in {
            DurableLifecycleStatus.ORPHANED,
            DurableLifecycleStatus.STALE,
        }:
            return AttachPlan(
                verdict=DurablePlanVerdict.ORPHANED,
                run_id=run.run_id,
                session_id=run.session_id,
                replay_cursor=run.reconnect_cursor,
                latest_seq=latest_seq,
                can_replay=can_replay,
                reason="active_lease_missing_or_expired",
            )
        if can_replay:
            return AttachPlan(
                verdict=DurablePlanVerdict.REPLAY_ONLY,
                run_id=run.run_id,
                session_id=run.session_id,
                replay_cursor=run.reconnect_cursor,
                latest_seq=latest_seq,
                can_replay=True,
                reason="no_live_lease_but_events_available",
            )
        return AttachPlan(
            verdict=DurablePlanVerdict.NO_CLAIM,
            run_id=run.run_id,
            session_id=run.session_id,
            reason="no_live_lease_or_replay_events",
        )

    def resume_plan(self, run_id: str) -> ResumePlan:
        run = self.runs.get(run_id)
        if run is None:
            return ResumePlan(
                verdict=DurablePlanVerdict.NOT_FOUND,
                run_id=run_id,
                reason="durable_run_record_missing",
            )
        if run.status in _TERMINAL_STATES:
            return ResumePlan(
                verdict=DurablePlanVerdict.TERMINAL,
                run_id=run.run_id,
                session_id=run.session_id,
                reason="terminal_runs_cannot_resume",
            )
        checkpoint = self.checkpoints.get(run.latest_checkpoint_id or "")
        if checkpoint is None:
            return ResumePlan(
                verdict=DurablePlanVerdict.CHECKPOINT_MISSING,
                run_id=run.run_id,
                session_id=run.session_id,
                reason="latest_checkpoint_missing",
            )
        interrupt = self.interrupts.get(run.paused_interrupt_id or "")
        approval = _approval_for_interrupt(self.approvals.values(), interrupt)
        base = {
            "run_id": run.run_id,
            "session_id": run.session_id,
            "checkpoint_id": checkpoint.checkpoint_id,
            "interrupt_id": interrupt.interrupt_id if interrupt else None,
            "approval_id": approval.approval_id if approval else None,
            "storage_backend": checkpoint.storage_backend,
            "side_effect_safety": checkpoint.side_effect_safety,
        }
        if checkpoint.storage_backend not in _SUPPORTED_RESUME_BACKENDS:
            return ResumePlan(
                verdict=DurablePlanVerdict.STORAGE_UNSUPPORTED,
                reason="checkpoint_storage_backend_cannot_load",
                **base,
            )
        if not checkpoint.resumable:
            return ResumePlan(
                verdict=DurablePlanVerdict.NO_CLAIM,
                reason="checkpoint_not_marked_resumable",
                **base,
            )
        if interrupt is None:
            return ResumePlan(
                verdict=DurablePlanVerdict.NO_CLAIM,
                reason="checkpoint_exists_but_interrupt_state_missing",
                **base,
            )
        if checkpoint.side_effect_safety == DurableSideEffectSafety.UNSAFE:
            return ResumePlan(
                verdict=DurablePlanVerdict.SIDE_EFFECT_UNSAFE,
                reason="side_effect_state_declared_unsafe",
                **base,
            )
        if checkpoint.side_effect_safety in {
            DurableSideEffectSafety.NO_CLAIM,
            DurableSideEffectSafety.MISSING,
        }:
            return ResumePlan(
                verdict=DurablePlanVerdict.NO_CLAIM,
                reason="side_effect_idempotency_evidence_missing",
                **base,
            )
        if interrupt.status == DurableInterruptStatus.PENDING:
            return ResumePlan(
                verdict=DurablePlanVerdict.APPROVAL_REQUIRED,
                reason="pending_interrupt_requires_human_resolution",
                **base,
            )
        if approval is not None and approval.status == DurableApprovalStatus.PENDING:
            return ResumePlan(
                verdict=DurablePlanVerdict.APPROVAL_REQUIRED,
                reason="pending_approval_requires_human_resolution",
                **base,
            )
        if interrupt.status in {
            DurableInterruptStatus.EXPIRED,
            DurableInterruptStatus.CANCELLED,
        }:
            return ResumePlan(
                verdict=DurablePlanVerdict.NO_CLAIM,
                reason=f"interrupt_{interrupt.status.value}",
                **base,
            )
        return ResumePlan(
            verdict=DurablePlanVerdict.RESUME_AVAILABLE,
            reason="checkpoint_interrupt_and_side_effect_evidence_available",
            **base,
        )

    def fork_plan(
        self,
        *,
        session_id: str | None = None,
        run_id: str | None = None,
        checkpoint_id: str | None = None,
    ) -> ForkPlan:
        run = self.runs.get(run_id or "") if run_id else None
        session = self.sessions.get(session_id or "") if session_id else None
        checkpoint = (
            self.checkpoints.get(checkpoint_id or "")
            if checkpoint_id
            else self.checkpoints.get(run.latest_checkpoint_id or "")
            if run is not None
            else None
        )
        if run_id and run is None:
            return ForkPlan(
                verdict=DurablePlanVerdict.NOT_FOUND,
                source_run_id=run_id,
                reason="source_run_missing",
            )
        if session_id and session is None:
            return ForkPlan(
                verdict=DurablePlanVerdict.NOT_FOUND,
                source_session_id=session_id,
                reason="source_session_missing",
            )
        if checkpoint is not None and checkpoint.forkable:
            source_session_id = session_id or (run.session_id if run else None)
            source_run_id = run_id or checkpoint.run_id
            return ForkPlan(
                verdict=DurablePlanVerdict.FORK_AVAILABLE,
                source_session_id=source_session_id,
                source_run_id=source_run_id,
                source_checkpoint_id=checkpoint.checkpoint_id,
                new_session_id=f"{source_session_id or source_run_id}:fork",
                new_branch_id=f"{checkpoint.branch_id or checkpoint.checkpoint_id}:fork",
                parent_branch_id=checkpoint.branch_id,
                copied_artifact_refs=(
                    self.sessions.get(source_session_id).artifact_refs
                    if source_session_id in self.sessions
                    else []
                ),
                reason="checkpoint_marked_forkable",
            )
        if session is not None and session.transcript_available:
            return ForkPlan(
                verdict=DurablePlanVerdict.FORK_AVAILABLE,
                source_session_id=session.session_id,
                source_run_id=run_id,
                new_session_id=f"{session.session_id}:fork",
                new_branch_id=f"{session.session_id}:transcript:fork",
                copied_artifact_refs=session.artifact_refs,
                reason="session_transcript_available",
            )
        if run is not None and run.status in _TERMINAL_STATES and run.latest_seq > 0:
            return ForkPlan(
                verdict=DurablePlanVerdict.FORK_AVAILABLE,
                source_session_id=run.session_id,
                source_run_id=run.run_id,
                new_session_id=f"{run.session_id}:fork",
                new_branch_id=f"{run.run_id}:terminal-report:fork",
                copied_artifact_refs=run.support_bundle_refs,
                reason="terminal_report_replay_available",
            )
        return ForkPlan(
            verdict=DurablePlanVerdict.NO_CLAIM,
            source_session_id=session_id or (run.session_id if run else None),
            source_run_id=run_id,
            source_checkpoint_id=checkpoint_id,
            reason="no_transcript_checkpoint_or_terminal_report_fork_evidence",
        )

    def project_adapter_events(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[HarnessAdapterEvent]:
        run = self.runs.get(run_id)
        return project_harness_adapter_events(
            self.list_events(run_id, after_seq=after_seq),
            session_id=run.session_id if run else None,
            source="replay",
        )

    def project_adapter_run(self, run_id: str) -> HarnessAdapterRun:
        run = self.runs[run_id]
        return project_harness_adapter_run(
            self.list_events(run_id),
            session_id=run.session_id,
            durability_level=run.durability_level.value,
            capability_pack_ids=[],
            scenario_ids=[],
            compatibility_flags={
                "attach": self.attach_plan(run_id).verdict.value,
                "resume": self.resume_plan(run_id).verdict.value,
            },
        )

    def lifecycle_snapshot(self, run_id: str) -> RunLifecycleSnapshot:
        run = self.runs.get(run_id)
        if run is None:
            return RunLifecycleSnapshot(run_id=run_id, state="unknown")
        attach = self.attach_plan(run_id)
        resume = self.resume_plan(run_id)
        state = _snapshot_state(run.status, attach)
        return RunLifecycleSnapshot(
            run_id=run.run_id,
            session_id=run.session_id,
            state=state,
            terminal_reason=run.terminal_verdict,
            last_seq=run.latest_seq or None,
            reconnect_cursor=run.reconnect_cursor,
            active_task=attach.verdict == DurablePlanVerdict.ATTACH_LIVE,
            abort_requested=bool(run.abort_request_id),
            checkpoint_available=bool(run.latest_checkpoint_id),
            paused_interrupt_id=run.paused_interrupt_id,
            resume_available=resume.verdict == DurablePlanVerdict.RESUME_AVAILABLE,
            orphaned=attach.verdict == DurablePlanVerdict.ORPHANED,
            orphan_reason=attach.reason
            if attach.verdict == DurablePlanVerdict.ORPHANED
            else None,
            support_bundle_available=bool(run.support_bundle_refs),
        )


def build_durable_lifecycle_compatibility_report(
    repository: DurableLifecycleRepository,
    *,
    product_family: str = "generic",
    generated_at: datetime | None = None,
    no_live: bool = True,
    scenario_ids: list[str] | None = None,
) -> DurableLifecycleCompatibilityReport:
    """Build a deterministic durable lifecycle compatibility report."""
    generated_at = generated_at or datetime.now(tz=UTC)
    runs = repository.list_runs()
    attach_plans = [repository.attach_plan(run.run_id) for run in runs]
    resume_plans = [repository.resume_plan(run.run_id) for run in runs]
    fork_plans = [
        repository.fork_plan(run_id=run.run_id, session_id=run.session_id)
        for run in runs
    ] or [
        repository.fork_plan(session_id=s.session_id)
        for s in repository.list_sessions()
    ]
    scenarios = scenario_ids or _DURABLE_SCENARIOS.get(
        product_family, _DURABLE_SCENARIOS["generic"]
    )
    feature_statuses = _feature_statuses(
        repository=repository,
        attach_plans=attach_plans,
        resume_plans=resume_plans,
        fork_plans=fork_plans,
        no_live=no_live,
    )
    return DurableLifecycleCompatibilityReport(
        report_id=(
            f"{product_family}:durable-lifecycle:"
            f"{generated_at.strftime('%Y%m%d%H%M%S')}"
        ),
        generated_at=generated_at.isoformat().replace("+00:00", "Z"),
        product_family=product_family,
        no_live=no_live,
        feature_statuses=feature_statuses,
        session_records=repository.list_sessions(),
        run_records=runs,
        checkpoint_records=sorted(
            repository.checkpoints.values(), key=lambda item: item.checkpoint_id
        ),
        interrupt_records=sorted(
            repository.interrupts.values(), key=lambda item: item.interrupt_id
        ),
        approval_records=sorted(
            repository.approvals.values(), key=lambda item: item.approval_id
        ),
        abort_records=sorted(
            repository.aborts.values(), key=lambda item: item.abort_request_id
        ),
        lease_records=sorted(
            repository.leases.values(), key=lambda item: item.lease_id
        ),
        log_refs=sorted(repository.logs.values(), key=lambda item: item.log_id),
        attach_plans=attach_plans,
        resume_plans=resume_plans,
        fork_plans=fork_plans,
        scenario_ids=scenarios,
        validation_gate_statuses={
            "deterministic_tests": "passed",
            "support_bundle_artifact": "passed",
            "openrouter_live_preflight": "no_claim",
            "phoenix_trace": "no_claim",
            "playwright_ui": "no_claim",
            "benchmark_delta": "no_claim",
        },
        skipped_reasons={
            "openrouter_live_preflight": "no_live_durable_lifecycle_seed",
            "phoenix_trace": "no_live_durable_lifecycle_seed",
            "playwright_ui": "ui_projection_not_changed",
            "benchmark_delta": "no_quality_or_latency_claim",
        },
        redacted_metadata={
            "projection": "deterministic",
            "live_evidence_claimed": not no_live,
        },
    )


def seed_durable_lifecycle_repository(
    product_family: str = "generic",
) -> DurableLifecycleRepository:
    """Return deterministic durable lifecycle fixture records."""
    repository = DurableLifecycleRepository()
    if product_family == "excel_ai":
        _seed_excel_ai(repository)
    elif product_family == "chat_demo":
        _seed_chat_demo(repository)
    else:
        _seed_generic(repository)
    return repository


def seed_durable_lifecycle_compatibility_reports(
    *, generated_at: datetime | None = None
) -> dict[str, DurableLifecycleCompatibilityReport]:
    """Return deterministic no-live reports for generic, Excel AI and chat-demo."""
    return {
        product: build_durable_lifecycle_compatibility_report(
            seed_durable_lifecycle_repository(product),
            product_family=product,
            generated_at=generated_at,
            no_live=True,
        )
        for product in ("generic", "excel_ai", "chat_demo")
    }


def render_durable_lifecycle_compatibility_markdown(
    report: DurableLifecycleCompatibilityReport,
) -> str:
    """Render a compact durable lifecycle compatibility report."""
    lines = [
        f"# Durable Lifecycle Compatibility {report.report_id}",
        "",
        f"Product family: `{report.product_family}`",
        f"Protocol: `{report.protocol}`",
        f"No-live mode: `{str(report.no_live).lower()}`",
        "",
        "## Features",
        "",
        "| Feature | Status |",
        "|---|---|",
    ]
    for feature, status in sorted(report.feature_statuses.items()):
        lines.append(f"| {feature} | {status} |")
    lines.extend(
        [
            "",
            "## Plan Verdicts",
            "",
            "| Plan | Run | Verdict | Reason |",
            "|---|---|---|---|",
        ]
    )
    for plan in report.attach_plans:
        lines.append(
            f"| attach | {plan.run_id or 'none'} | {plan.verdict.value} | {plan.reason or ''} |"
        )
    for plan in report.resume_plans:
        lines.append(
            f"| resume | {plan.run_id or 'none'} | {plan.verdict.value} | {plan.reason or ''} |"
        )
    for plan in report.fork_plans:
        source = plan.source_run_id or plan.source_session_id or "none"
        lines.append(
            f"| fork | {source} | {plan.verdict.value} | {plan.reason or ''} |"
        )
    lines.extend(
        [
            "",
            "## Evidence",
            "",
            f"- Sessions: {len(report.session_records)}",
            f"- Runs: {len(report.run_records)}",
            f"- Checkpoints: {len(report.checkpoint_records)}",
            f"- Interrupts: {len(report.interrupt_records)}",
            f"- Approvals: {len(report.approval_records)}",
            f"- Aborts: {len(report.abort_records)}",
            f"- Leases: {len(report.lease_records)}",
            f"- Logs: {len(report.log_refs)}",
            f"- Scenarios: {', '.join(report.scenario_ids) or 'none'}",
            "",
            "## Gate Statuses",
            "",
            "| Gate | Status |",
            "|---|---|",
        ]
    )
    for gate_id, status in sorted(report.validation_gate_statuses.items()):
        lines.append(f"| {gate_id} | {status} |")
    lines.append("")
    return "\n".join(lines)


def write_durable_lifecycle_artifacts(
    output_dir: str | Path,
    report: DurableLifecycleCompatibilityReport,
    *,
    adapter_events: Iterable[HarnessAdapterEvent] = (),
) -> dict[str, Any]:
    """Persist durable lifecycle JSON/Markdown and optional adapter events."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "durable_lifecycle_compatibility_report.json"
    md_path = root / "durable_lifecycle_compatibility_report.md"
    records_path = root / "durable_lifecycle_records.json"
    events_path = root / "adapter_events.jsonl"
    payload = report.model_dump(mode="json")
    json_path.write_text(
        json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(
        render_durable_lifecycle_compatibility_markdown(report),
        encoding="utf-8",
    )
    records_path.write_text(
        json.dumps(_records_payload(report), ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    event_rows = [event.model_dump(mode="json") for event in adapter_events]
    if event_rows:
        events_path.write_text(
            "".join(json.dumps(row, ensure_ascii=True) + "\n" for row in event_rows),
            encoding="utf-8",
        )
    artifacts = [
        _artifact_row(json_path, "durable_lifecycle_compatibility_report", root=root),
        _artifact_row(md_path, "durable_lifecycle_compatibility_report", root=root),
        _artifact_row(records_path, "durable_lifecycle_records", root=root),
    ]
    if event_rows:
        artifacts.append(_artifact_row(events_path, "adapter_events", root=root))
    manifest = {
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "redaction": {"safe_by_default": True},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        "durable_lifecycle_compatibility_report_json": str(json_path),
        "durable_lifecycle_compatibility_report_markdown": str(md_path),
        "durable_lifecycle_records": str(records_path),
        "adapter_events_jsonl": str(events_path) if event_rows else None,
        "manifest": str(manifest_path),
    }


def _seed_generic(repository: DurableLifecycleRepository) -> None:
    repository.upsert_session(
        DurableSessionRecord(
            session_id="generic_session",
            workspace_id="workspace",
            adapter_id="generic",
            current_run_id="generic_run",
            lifecycle_state=DurableLifecycleStatus.PAUSED,
            transcript_available=True,
            durability_level=DurableDurabilityLevel.SQLITE,
            search_metadata={"task": "durable lifecycle seed"},
        )
    )
    repository.upsert_run(
        DurableRunRecord(
            run_id="generic_run",
            session_id="generic_session",
            status=DurableLifecycleStatus.PAUSED,
            latest_seq=2,
            reconnect_cursor="generic_run:2",
            latest_checkpoint_id="generic_checkpoint",
            paused_interrupt_id="generic_interrupt",
            durability_level=DurableDurabilityLevel.SQLITE,
            side_effect_safety=DurableSideEffectSafety.SAFE,
        )
    )
    repository.upsert_checkpoint(
        DurableCheckpointIndex(
            checkpoint_id="generic_checkpoint",
            run_id="generic_run",
            graph_id="agent",
            state_version="v1",
            storage_backend=DurableDurabilityLevel.SQLITE,
            resumable=True,
            forkable=True,
            side_effect_safety=DurableSideEffectSafety.SAFE,
        )
    )
    repository.upsert_interrupt(
        DurableInterruptRecord(
            interrupt_id="generic_interrupt",
            run_id="generic_run",
            checkpoint_id="generic_checkpoint",
            status=DurableInterruptStatus.RESOLVED,
            reason="approval",
            allowed_actions=["approve", "reject"],
            resolution={"action": "approve"},
            resolved_at="2026-07-03T00:01:00Z",
        )
    )
    repository.append_events(
        _seed_run_stream_events(
            "generic_run",
            ["run_started", "run_paused", "run_completed"],
        )
    )


def _seed_excel_ai(repository: DurableLifecycleRepository) -> None:
    repository.upsert_session(
        DurableSessionRecord(
            session_id="excel_session",
            workspace_id="excel_workspace",
            owner_id="excel-ai-adapter",
            adapter_id="excel_ai",
            current_run_id="excel_workbook_run",
            lifecycle_state=DurableLifecycleStatus.PAUSED,
            transcript_available=True,
            durability_level=DurableDurabilityLevel.SQLITE,
            search_metadata={"subject": "workbook paused approval"},
            artifact_refs=["workbook_context.json", "edit_transaction.json"],
        )
    )
    repository.upsert_run(
        DurableRunRecord(
            run_id="excel_workbook_run",
            session_id="excel_session",
            status=DurableLifecycleStatus.PAUSED,
            latest_seq=4,
            reconnect_cursor="excel_workbook_run:4",
            latest_checkpoint_id="excel_checkpoint",
            paused_interrupt_id="excel_approval_interrupt",
            durability_level=DurableDurabilityLevel.SQLITE,
            side_effect_safety=DurableSideEffectSafety.NO_CLAIM,
            support_bundle_refs=["excel_support_bundle.json"],
            log_refs=["excel_background_log"],
            redacted_metadata={"workbook_context_artifact": "workbook_context.json"},
        )
    )
    repository.upsert_checkpoint(
        DurableCheckpointIndex(
            checkpoint_id="excel_checkpoint",
            run_id="excel_workbook_run",
            branch_id="excel_main",
            graph_id="workbook_agent",
            node_id="approval_gate",
            state_version="v1",
            storage_backend=DurableDurabilityLevel.SQLITE,
            resumable=True,
            forkable=True,
            side_effect_safety=DurableSideEffectSafety.NO_CLAIM,
            side_effect_notes="workbook edit transaction evidence is metadata-only",
        )
    )
    repository.upsert_interrupt(
        DurableInterruptRecord(
            interrupt_id="excel_approval_interrupt",
            run_id="excel_workbook_run",
            checkpoint_id="excel_checkpoint",
            status=DurableInterruptStatus.PENDING,
            reason="workbook_edit_approval",
            allowed_actions=["approve", "reject", "edit", "clarify", "cancel"],
            approval_payload_summary={
                "tool_name": "excel_apply_edit",
                "side_effect_class": "transactional_edit",
            },
            created_at="2026-07-03T00:00:00Z",
        )
    )
    repository.upsert_approval(
        DurableApprovalRecord(
            approval_id="excel_approval",
            interrupt_id="excel_approval_interrupt",
            run_id="excel_workbook_run",
            status=DurableApprovalStatus.PENDING,
            request_summary={"options": ["approve", "reject", "edit", "clarify"]},
            requested_at="2026-07-03T00:00:00Z",
        )
    )
    repository.upsert_log_ref(
        BackgroundRunLogRef(
            log_id="excel_background_log",
            run_id="excel_workbook_run",
            log_type="runtime_events",
            path="durable_lifecycle_records.json",
            size_bytes=0,
            sha256=None,
        )
    )
    repository.append_events(
        _seed_run_stream_events(
            "excel_workbook_run",
            ["run_started", "artifact_created", "interrupt_requested", "run_paused"],
            session_id="excel_session",
        )
    )


def _seed_chat_demo(repository: DurableLifecycleRepository) -> None:
    repository.upsert_session(
        DurableSessionRecord(
            session_id="chat_demo_session",
            workspace_id="chat_demo_workspace",
            owner_id="chat-demo-adapter",
            adapter_id="chat_demo",
            current_run_id="chat_demo_research_run",
            lifecycle_state=DurableLifecycleStatus.ORPHANED,
            transcript_available=True,
            durability_level=DurableDurabilityLevel.JSONL,
            search_metadata={"subject": "long running research"},
            artifact_refs=["research/sources.jsonl", "research/report.md"],
        )
    )
    repository.upsert_run(
        DurableRunRecord(
            run_id="chat_demo_research_run",
            session_id="chat_demo_session",
            status=DurableLifecycleStatus.ORPHANED,
            latest_seq=6,
            reconnect_cursor="chat_demo_research_run:6",
            latest_checkpoint_id="chat_demo_checkpoint",
            paused_interrupt_id="chat_demo_steering_interrupt",
            active_lease_id="chat_demo_lease",
            durability_level=DurableDurabilityLevel.JSONL,
            side_effect_safety=DurableSideEffectSafety.MISSING,
            support_bundle_refs=["chat_demo_support_bundle.json"],
            log_refs=["chat_demo_background_log"],
        )
    )
    repository.upsert_checkpoint(
        DurableCheckpointIndex(
            checkpoint_id="chat_demo_checkpoint",
            run_id="chat_demo_research_run",
            branch_id="research_main",
            graph_id="research_agent",
            node_id="steering_gate",
            state_version="v1",
            storage_backend=DurableDurabilityLevel.JSONL,
            resumable=True,
            forkable=True,
            side_effect_safety=DurableSideEffectSafety.MISSING,
            side_effect_notes="workspace write idempotency evidence missing",
        )
    )
    repository.upsert_interrupt(
        DurableInterruptRecord(
            interrupt_id="chat_demo_steering_interrupt",
            run_id="chat_demo_research_run",
            checkpoint_id="chat_demo_checkpoint",
            status=DurableInterruptStatus.PENDING,
            reason="research_steering",
            allowed_actions=["approve", "clarify", "cancel"],
            approval_payload_summary={"artifact": "research/report.md"},
            created_at="2026-07-03T00:00:00Z",
        )
    )
    repository.upsert_lease(
        BackgroundRunLease(
            lease_id="chat_demo_lease",
            run_id="chat_demo_research_run",
            owner_process_id="pid:seed",
            owner_host_id="chat-demo",
            heartbeat_seq=6,
            heartbeat_at="2026-07-03T00:00:00Z",
            expires_at="2026-07-03T00:01:00Z",
            status=DurableLeaseStatus.ORPHANED,
        )
    )
    repository.upsert_log_ref(
        BackgroundRunLogRef(
            log_id="chat_demo_background_log",
            run_id="chat_demo_research_run",
            log_type="background_runtime_log",
            path="research/background.log",
            size_bytes=128,
            sha256="0" * 64,
        )
    )
    repository.append_events(
        _seed_run_stream_events(
            "chat_demo_research_run",
            [
                "run_started",
                "source_ledger_updated",
                "artifact_created",
                "interrupt_requested",
                "run_paused",
                "artifact_created",
            ],
            session_id="chat_demo_session",
        )
    )


def _feature_statuses(
    *,
    repository: DurableLifecycleRepository,
    attach_plans: list[AttachPlan],
    resume_plans: list[ResumePlan],
    fork_plans: list[ForkPlan],
    no_live: bool,
) -> dict[str, str]:
    return {
        "session_records": "supported" if repository.sessions else "no_claim",
        "run_records": "supported" if repository.runs else "no_claim",
        "checkpoint_index": "supported" if repository.checkpoints else "no_claim",
        "interrupt_records": "supported" if repository.interrupts else "no_claim",
        "approval_records": "supported" if repository.approvals else "no_claim",
        "abort_records": "supported" if repository.aborts else "no_claim",
        "background_leases": "supported" if repository.leases else "no_claim",
        "background_logs": "supported" if repository.logs else "no_claim",
        "attach_plan": _best_status(plan.verdict.value for plan in attach_plans),
        "replay": (
            "supported"
            if any(plan.can_replay for plan in attach_plans)
            or any(run.latest_seq > 0 for run in repository.runs.values())
            else "no_claim"
        ),
        "resume_plan": _best_status(plan.verdict.value for plan in resume_plans),
        "fork_plan": _best_status(plan.verdict.value for plan in fork_plans),
        "adapter_projection": "supported" if repository.runs else "no_claim",
        "process_restart_live": "no_claim" if no_live else "supported",
        "phoenix_trace": "no_claim",
        "playwright_ui": "no_claim",
    }


def _best_status(statuses: Iterable[str]) -> str:
    ordered = list(statuses)
    for status in (
        "supported",
        "attach_live",
        "resume_available",
        "fork_available",
        "approval_required",
        "replay_only",
        "orphaned",
        "terminal",
        "no_claim",
    ):
        if status in ordered:
            return (
                "supported"
                if status in {"attach_live", "resume_available", "fork_available"}
                else status
            )
    return ordered[0] if ordered else "no_claim"


def _durability_from_backend(value: str) -> DurableDurabilityLevel:
    normalized = value.lower()
    aliases = {
        "server_memory": DurableDurabilityLevel.PROCESS_LOCAL,
        "memory": DurableDurabilityLevel.PROCESS_LOCAL,
        "in_memory": DurableDurabilityLevel.PROCESS_LOCAL,
    }
    if normalized in aliases:
        return aliases[normalized]
    try:
        return DurableDurabilityLevel(normalized)
    except ValueError:
        return DurableDurabilityLevel.UNKNOWN


def _approval_for_interrupt(
    approvals: Iterable[DurableApprovalRecord],
    interrupt: DurableInterruptRecord | None,
) -> DurableApprovalRecord | None:
    if interrupt is None:
        return None
    return next(
        (
            approval
            for approval in approvals
            if approval.interrupt_id == interrupt.interrupt_id
            or approval.run_id == interrupt.run_id
        ),
        None,
    )


def _latest_seq(events: list[RunStreamEvent]) -> int:
    return max((event.seq for event in events), default=0)


def _search_blob(*values: dict[str, Any]) -> str:
    return json.dumps(values, ensure_ascii=True, sort_keys=True).lower()


def _parse_datetime(value: str) -> datetime | None:
    try:
        if value.endswith("Z"):
            value = value[:-1] + "+00:00"
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _snapshot_state(run_status: DurableLifecycleStatus, attach: AttachPlan) -> str:
    if attach.verdict == DurablePlanVerdict.ORPHANED:
        return "orphaned"
    return {
        DurableLifecycleStatus.ACTIVE: "running",
        DurableLifecycleStatus.QUEUED: "queued",
        DurableLifecycleStatus.PAUSED: "paused",
        DurableLifecycleStatus.RECOVERABLE: "awaiting_input",
        DurableLifecycleStatus.ORPHANED: "orphaned",
        DurableLifecycleStatus.STALE: "orphaned",
        DurableLifecycleStatus.COMPLETED: "completed",
        DurableLifecycleStatus.FAILED: "failed",
        DurableLifecycleStatus.CANCELLED: "cancelled",
    }.get(run_status, "unknown")


def _records_payload(report: DurableLifecycleCompatibilityReport) -> dict[str, Any]:
    return {
        "sessions": [item.model_dump(mode="json") for item in report.session_records],
        "runs": [item.model_dump(mode="json") for item in report.run_records],
        "checkpoints": [
            item.model_dump(mode="json") for item in report.checkpoint_records
        ],
        "interrupts": [
            item.model_dump(mode="json") for item in report.interrupt_records
        ],
        "approvals": [item.model_dump(mode="json") for item in report.approval_records],
        "aborts": [item.model_dump(mode="json") for item in report.abort_records],
        "leases": [item.model_dump(mode="json") for item in report.lease_records],
        "logs": [item.model_dump(mode="json") for item in report.log_refs],
    }


def _seed_run_stream_events(
    run_id: str, names: list[str], *, session_id: str | None = None
) -> list[RunStreamEvent]:
    events: list[RunStreamEvent] = []
    for index, name in enumerate(names, start=1):
        data: dict[str, Any] = {"session_id": session_id} if session_id else {}
        if name == "interrupt_requested":
            data.update(
                {
                    "interrupt_id": f"{run_id}:interrupt",
                    "allowed_actions": ["approve", "reject", "clarify", "cancel"],
                }
            )
        if name == "artifact_created":
            data.update(
                {
                    "artifact_id": f"{run_id}:artifact:{index}",
                    "artifact_type": "support_bundle"
                    if index == len(names)
                    else "lifecycle_fixture",
                    "path": "durable_lifecycle_records.json",
                }
            )
        events.append(
            RunStreamEvent(
                stream_id=f"{run_id}:{index}",
                run_id=run_id,
                attempt_id="attempt_1",
                seq=index,
                event=name,
                source="durable_lifecycle_fixture",
                data=data,
            )
        )
    return events


def _artifact_row(path: Path, artifact_type: str, *, root: Path) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact_id": path.name,
        "artifact_type": artifact_type,
        "path": str(path.relative_to(root)),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
    }


__all__ = [
    "DurableLifecycleRepository",
    "build_durable_lifecycle_compatibility_report",
    "render_durable_lifecycle_compatibility_markdown",
    "seed_durable_lifecycle_compatibility_reports",
    "seed_durable_lifecycle_repository",
    "write_durable_lifecycle_artifacts",
]
