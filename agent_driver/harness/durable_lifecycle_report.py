"""Durable-lifecycle compatibility report: build + markdown render + artifact write.

Extracted from ``harness/durable_lifecycle`` (god-module split, behaviour-neutral). Operates
on a repository passed in (TYPE_CHECKING-only class import to avoid a cycle) and its records;
re-exported from ``durable_lifecycle`` for existing callers.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_driver.contracts.durable_lifecycle import (
    AttachPlan,
    DurableLifecycleCompatibilityReport,
    ForkPlan,
    ResumePlan,
)
from agent_driver.contracts.harness_adapter import HarnessAdapterEvent
from agent_driver.runtime.validation_artifacts import artifact_manifest_row

if TYPE_CHECKING:
    from agent_driver.harness.durable_lifecycle import DurableLifecycleRepository


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
        artifact_manifest_row(json_path, "durable_lifecycle_compatibility_report", root=root, include_id=True),
        artifact_manifest_row(md_path, "durable_lifecycle_compatibility_report", root=root, include_id=True),
        artifact_manifest_row(records_path, "durable_lifecycle_records", root=root, include_id=True),
    ]
    if event_rows:
        artifacts.append(artifact_manifest_row(events_path, "adapter_events", root=root, include_id=True))
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




__all__ = [
    "build_durable_lifecycle_compatibility_report",
    "render_durable_lifecycle_compatibility_markdown",
    "write_durable_lifecycle_artifacts",
]
