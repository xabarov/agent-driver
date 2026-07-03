"""Offline continuous-validation audit over capability-pack evidence indexes."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from agent_driver.contracts.capability_packs import EvidenceArtifactIndex
from agent_driver.contracts.continuous_validation import (
    FlakeRecord,
    HarnessBaseline,
    HostAdoptionState,
    RegressionSummary,
    ReleaseGatePolicy,
    ValidationArtifactRef,
    ValidationDashboardSummary,
    ValidationRunRecord,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.harness.capability_packs import seed_scenario_specs

_LIVE_GATE_IDS = frozenset(
    {
        "openrouter_live_preflight",
        "phoenix_trace",
        "playwright_ui",
        "benchmark_delta",
    }
)
_REQUIRED_DETERMINISTIC_GATE_IDS = frozenset(
    {"deterministic_tests", "support_bundle_artifact"}
)
_DEFAULT_FRESHNESS_WINDOWS = {
    "deterministic_tests": 168,
    "support_bundle_artifact": 168,
    "openrouter_live_preflight": 24,
    "phoenix_trace": 24,
    "playwright_ui": 72,
    "benchmark_delta": 168,
}


def seed_harness_baselines() -> dict[str, HarnessBaseline]:
    """Return file-backed seed baselines for the two product validation targets."""
    return {
        "excel_workbook_chat.baseline.v1": HarnessBaseline(
            baseline_id="excel_workbook_chat.baseline.v1",
            pack_id="excel_workbook_chat",
            product_adapter_id="excel_ai",
            scenario_ids=["excel.workbook_context.transaction.v1"],
            expected_gate_statuses={
                "deterministic_tests": "passed",
                "support_bundle_artifact": "passed",
                "openrouter_live_preflight": "no_claim",
                "phoenix_trace": "no_claim",
                "playwright_ui": "no_claim",
                "benchmark_delta": "no_claim",
            },
            required_artifact_ids=[
                "evidence_index.json",
                "validation_gates.json",
                "manifest.json",
            ],
            required_evidence=[
                "context_provenance",
                "artifact_provenance",
                "side_effect_transactions",
                "validation_gates",
            ],
            gate_freshness_windows_hours=dict(_DEFAULT_FRESHNESS_WINDOWS),
            cost_latency_bounds={"max_cost_usd": 0, "live_claims": "opt_in"},
            owner="excel-ai-adapter",
            review_after="2026-08-03",
            redacted_metadata={
                "source": "seed_capability_pack",
                "live_gates": "no_claim_until_explicit_execution",
            },
        ),
        "deep_research_chat_demo.baseline.v1": HarnessBaseline(
            baseline_id="deep_research_chat_demo.baseline.v1",
            pack_id="deep_research_chat_demo",
            product_adapter_id="chat_demo",
            scenario_ids=["chat_demo.deep_research.source_report.v1"],
            expected_gate_statuses={
                "deterministic_tests": "passed",
                "support_bundle_artifact": "passed",
                "openrouter_live_preflight": "no_claim",
                "phoenix_trace": "no_claim",
                "playwright_ui": "no_claim",
            },
            required_artifact_ids=[
                "evidence_index.json",
                "validation_gates.json",
                "manifest.json",
            ],
            required_evidence=[
                "source_evidence",
                "artifact_provenance",
                "validation_gates",
            ],
            gate_freshness_windows_hours=dict(_DEFAULT_FRESHNESS_WINDOWS),
            cost_latency_bounds={"max_cost_usd": 0, "live_claims": "opt_in"},
            owner="chat-demo-adapter",
            review_after="2026-08-03",
            redacted_metadata={
                "source": "seed_capability_pack",
                "live_gates": "no_claim_until_explicit_execution",
            },
        ),
    }


def seed_release_gate_policies() -> dict[str, ReleaseGatePolicy]:
    """Return initial blast-radius release gate policies."""
    deterministic_required = ["deterministic_tests", "support_bundle_artifact"]
    return {
        "deterministic_metadata_change": ReleaseGatePolicy(
            policy_id="deterministic_metadata_change",
            change_types=["docs", "metadata", "capability_pack"],
            required_gate_ids=deterministic_required,
            optional_gate_ids=sorted(_LIVE_GATE_IDS),
            stale_allowed_gate_ids=sorted(_LIVE_GATE_IDS),
            max_cost_usd=0,
            timeout_seconds=60,
            retry_budget=0,
        ),
        "provider_behavior_change": ReleaseGatePolicy(
            policy_id="provider_behavior_change",
            change_types=["provider", "policy", "runtime_behavior"],
            required_gate_ids=deterministic_required,
            live_required_gate_ids=["openrouter_live_preflight", "phoenix_trace"],
            optional_gate_ids=["playwright_ui", "benchmark_delta"],
            max_cost_usd=1.0,
            timeout_seconds=300,
            retry_budget=1,
        ),
        "ui_or_quality_change": ReleaseGatePolicy(
            policy_id="ui_or_quality_change",
            change_types=["ui", "quality", "benchmark"],
            required_gate_ids=deterministic_required,
            ui_required_gate_ids=["playwright_ui"],
            benchmark_required_gate_ids=["benchmark_delta"],
            optional_gate_ids=["openrouter_live_preflight", "phoenix_trace"],
            max_cost_usd=2.0,
            timeout_seconds=600,
            retry_budget=1,
        ),
    }


def seed_host_adoption_states() -> dict[str, HostAdoptionState]:
    """Return metadata-only adoption profiles for the two seed products."""
    return {
        "excel_ai:excel_workbook_chat": HostAdoptionState(
            product_adapter_id="excel_ai",
            pack_id="excel_workbook_chat",
            status="metadata_only",
            scenario_ids=["excel.workbook_context.transaction.v1"],
            metadata_paths=[
                "capability_pack_resolution",
                "runtime metadata capability_pack_id",
                "validation_run_record pack_ids/scenario_ids",
            ],
            required_gate_ids=[
                "deterministic_tests",
                "support_bundle_artifact",
            ],
            rollback_notes=[
                "Omit capability-pack metadata from Excel backend validation calls.",
                "Continue product-owned pytest and benchmark gates without pack ids.",
            ],
            owner="excel-ai-adapter",
        ),
        "chat_demo:deep_research_chat_demo": HostAdoptionState(
            product_adapter_id="chat_demo",
            pack_id="deep_research_chat_demo",
            status="metadata_only",
            scenario_ids=["chat_demo.deep_research.source_report.v1"],
            metadata_paths=[
                "capability_pack_resolution",
                "trace summary capability_pack_id",
                "validation_run_record pack_ids/scenario_ids",
            ],
            required_gate_ids=[
                "deterministic_tests",
                "support_bundle_artifact",
            ],
            rollback_notes=[
                "Omit capability-pack metadata from chat-demo trace summaries.",
                "Continue source/report deterministic probes without pack ids.",
            ],
            owner="chat-demo-adapter",
        ),
    }


def seed_flake_records() -> dict[str, FlakeRecord]:
    """Return initial quarantine fixtures documenting the list format."""
    return {
        "example.playwright_ui.quarantine": FlakeRecord(
            flake_id="example.playwright_ui.quarantine",
            scenario_id="chat_demo.deep_research.source_report.v1",
            gate_id="playwright_ui",
            owner="chat-demo-adapter",
            first_seen="2026-07-03",
            last_seen="2026-07-03",
            quarantined=True,
            quarantine_expires="2026-07-17",
            repro_command=(
                "CHAT_DEMO_URL=http://localhost:5174 uv run python "
                "examples/chat-demo/frontend/tests/e2e/chat_live_probe.py "
                "--scenario research-report"
            ),
            evidence_links=["validation_report.md"],
            promotion_notes=[
                "Remove the quarantine after two consecutive clean live UI probes.",
                "Keep the gate visible in dashboard summaries while quarantined.",
            ],
        )
    }


def audit_validation_evidence(
    evidence_index_dirs: list[str | Path],
    *,
    baseline_ids: list[str] | None = None,
    flake_records: list[FlakeRecord] | None = None,
    strict: bool = False,
    no_live: bool = False,
    now: datetime | None = None,
    run_id: str | None = None,
) -> dict[str, Any]:
    """Read persisted evidence-index directories and build validation reports."""
    now = now or datetime.now(timezone.utc)
    roots = [Path(item) for item in evidence_index_dirs]
    if not roots:
        raise ValueError("at least one evidence index directory is required")

    loaded = [_load_evidence_directory(root) for root in roots]
    baselines = _select_baselines(loaded, baseline_ids=baseline_ids)
    gate_results, skip_reasons = _combined_gate_results(
        loaded,
        no_live=no_live,
        flake_records=flake_records or [],
        now=now,
    )
    artifact_refs = [
        ref
        for item in loaded
        for ref in item["artifact_refs"]
        if isinstance(ref, ValidationArtifactRef)
    ]
    corrupt_artifacts = [
        item
        for loaded_item in loaded
        for item in loaded_item["corrupt_artifacts"]
        if isinstance(item, str)
    ]
    stale_gates = _stale_gates(loaded, gate_results, baselines, now=now)
    validation_run = ValidationRunRecord(
        run_id=run_id or f"validation-{now.strftime('%Y%m%d%H%M%S')}",
        baseline_ids=[baseline.baseline_id for baseline in baselines],
        pack_ids=_sorted_unique(
            index.pack_id
            for item in loaded
            for index in [item["evidence_index"]]
            if isinstance(index, EvidenceArtifactIndex) and index.pack_id
        ),
        scenario_ids=_sorted_unique(
            scenario_id
            for item in loaded
            for scenario_id in item["evidence_index"].scenario_ids
        ),
        product_adapter_ids=_adapter_ids_for_loaded_indexes(loaded),
        artifact_index_refs=artifact_refs,
        gate_results=gate_results,
        ended_at=now.isoformat(),
        skip_reasons=skip_reasons,
        redacted_metadata={
            "strict": strict,
            "no_live": no_live,
            "evidence_index_dirs": [str(root) for root in roots],
        },
    )
    regression = _build_regression_summary(
        validation_run,
        baselines,
        stale_gates=stale_gates,
        corrupt_artifacts=corrupt_artifacts,
    )
    dashboard = _build_dashboard_summary(validation_run, regression)
    return {
        "validation_run": validation_run.model_dump(mode="json"),
        "regression_summary": regression.model_dump(mode="json"),
        "dashboard_summary": dashboard.model_dump(mode="json"),
        "strict": strict,
        "strict_passed": (
            regression.candidate_status not in {"failed", "blocked", "stale"}
        ),
        "markdown_report": render_validation_markdown(
            validation_run, regression, dashboard
        ),
        "redaction": {"safe_by_default": True},
    }


def write_validation_audit_report(
    output_dir: str | Path,
    audit_payload: dict[str, Any],
) -> dict[str, str]:
    """Persist JSON and Markdown reports for an audit payload."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "validation_run.json"
    md_path = root / "validation_report.md"
    serializable = {
        key: value
        for key, value in audit_payload.items()
        if key != "markdown_report"
    }
    json_path.write_text(
        json.dumps(serializable, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    md_path.write_text(str(audit_payload["markdown_report"]), encoding="utf-8")
    return {
        "validation_run_json": str(json_path),
        "validation_report_markdown": str(md_path),
    }


def render_validation_markdown(
    validation_run: ValidationRunRecord,
    regression: RegressionSummary,
    dashboard: ValidationDashboardSummary,
) -> str:
    """Render a compact Markdown validation dashboard."""
    lines = [
        f"# Validation Audit {validation_run.run_id}",
        "",
        f"Candidate status: `{dashboard.candidate_status}`",
        "",
        "## Products",
        "",
        "| Product | Pack | Scenarios | Deterministic | Live |",
        "|---|---|---|---|---|",
    ]
    for row in dashboard.product_rows:
        lines.append(
            "| {product} | {pack} | {scenarios} | {deterministic} | {live} |".format(
                product=row.get("product_adapter_id", ""),
                pack=row.get("pack_id", ""),
                scenarios=", ".join(row.get("scenario_ids", [])),
                deterministic=row.get("deterministic_status", ""),
                live=row.get("live_status", ""),
            )
        )
    lines.extend(
        [
            "",
            "## Shared Gates",
            "",
            "| Gate | Status | Reason |",
            "|---|---|---|",
        ]
    )
    for gate in validation_run.gate_results:
        lines.append(
            f"| {gate.gate_id} | {gate.status} | {gate.reason or ''} |"
        )
    lines.extend(
        [
            "",
            "## Regression",
            "",
            f"- New failures: {', '.join(regression.new_failures) or 'none'}",
            f"- Missing artifacts: {', '.join(regression.missing_artifacts) or 'none'}",
            f"- Corrupt artifacts: {', '.join(regression.corrupt_artifacts) or 'none'}",
            f"- Stale gates: {', '.join(regression.stale_gates) or 'none'}",
            f"- No-claim gates: {', '.join(regression.no_claim_gates) or 'none'}",
            "",
            "## Artifacts",
            "",
        ]
    )
    for artifact_path in dashboard.artifact_paths:
        lines.append(f"- `{artifact_path}`")
    if not dashboard.artifact_paths:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def _load_evidence_directory(root: Path) -> dict[str, Any]:
    index_path = root / "evidence_index.json" if root.is_dir() else root
    if not index_path.is_file():
        raise ValueError(f"missing evidence_index.json: {root}")
    evidence_index = EvidenceArtifactIndex.model_validate(
        json.loads(index_path.read_text(encoding="utf-8"))
    )
    artifact_refs: list[ValidationArtifactRef] = [
        ValidationArtifactRef(
            artifact_id="evidence_index.json",
            artifact_type="evidence_index",
            path=str(index_path),
            sha256=_sha256(index_path),
            size_bytes=index_path.stat().st_size,
        )
    ]
    corrupt_artifacts: list[str] = []
    manifest_path = index_path.parent / "manifest.json"
    if manifest_path.is_file():
        artifact_refs.append(
            ValidationArtifactRef(
                artifact_id="manifest.json",
                artifact_type="manifest",
                path=str(manifest_path),
                sha256=_sha256(manifest_path),
                size_bytes=manifest_path.stat().st_size,
            )
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for row in manifest.get("artifacts", []):
            if not isinstance(row, dict):
                continue
            relative_path = row.get("path")
            artifact_type = str(row.get("artifact_type") or "other")
            artifact_id = str(relative_path or artifact_type)
            artifact_path = (
                index_path.parent / relative_path
                if isinstance(relative_path, str)
                else None
            )
            expected_sha = row.get("sha256")
            expected_size = row.get("size_bytes")
            gate_id = _gate_id_for_artifact_type(artifact_type)
            ref = ValidationArtifactRef(
                artifact_id=artifact_id,
                artifact_type=_known_artifact_type(artifact_type),
                path=str(artifact_path) if artifact_path is not None else None,
                sha256=str(expected_sha) if expected_sha else None,
                size_bytes=expected_size if isinstance(expected_size, int) else None,
                gate_id=gate_id,
            )
            artifact_refs.append(ref)
            if artifact_path is None or not artifact_path.is_file():
                corrupt_artifacts.append(artifact_id)
                continue
            actual_size = artifact_path.stat().st_size
            actual_sha = _sha256(artifact_path)
            if expected_size is not None and actual_size != expected_size:
                corrupt_artifacts.append(artifact_id)
            if expected_sha is not None and actual_sha != expected_sha:
                corrupt_artifacts.append(artifact_id)
    for artifact in evidence_index.artifacts:
        artifact_refs.append(
            ValidationArtifactRef(
                artifact_id=artifact.artifact_id,
                artifact_type=_known_artifact_type(artifact.artifact_type),
                path=artifact.path,
                uri=artifact.uri,
                sha256=artifact.sha256,
                gate_id=artifact.gate_id,
                redacted_metadata=artifact.redacted_metadata,
            )
        )
    return {
        "root": index_path.parent,
        "index_path": index_path,
        "evidence_index": evidence_index,
        "artifact_refs": artifact_refs,
        "corrupt_artifacts": sorted(set(corrupt_artifacts)),
        "observed_at": datetime.fromtimestamp(
            index_path.stat().st_mtime, tz=timezone.utc
        ),
    }


def _select_baselines(
    loaded: list[dict[str, Any]], *, baseline_ids: list[str] | None
) -> list[HarnessBaseline]:
    all_baselines = seed_harness_baselines()
    if baseline_ids:
        missing = [item for item in baseline_ids if item not in all_baselines]
        if missing:
            raise ValueError(f"unknown baseline ids: {', '.join(missing)}")
        return [all_baselines[item] for item in baseline_ids]
    pack_ids = {
        item["evidence_index"].pack_id
        for item in loaded
        if isinstance(item["evidence_index"], EvidenceArtifactIndex)
    }
    return [
        baseline
        for baseline in all_baselines.values()
        if baseline.pack_id in pack_ids
    ]


def _combined_gate_results(
    loaded: list[dict[str, Any]],
    *,
    no_live: bool,
    flake_records: list[FlakeRecord],
    now: datetime,
) -> tuple[list[ValidationGateResult], dict[str, str]]:
    by_id: dict[str, ValidationGateResult] = {}
    skip_reasons: dict[str, str] = {}
    loaded_scenario_ids = {
        scenario_id
        for item in loaded
        for scenario_id in item["evidence_index"].scenario_ids
    }
    for item in loaded:
        index = item["evidence_index"]
        for gate in index.gates:
            status = gate.status
            reason = gate.reason
            if no_live and gate.gate_id in _LIVE_GATE_IDS and status in {
                "skipped",
                "not_run",
            }:
                status = "no_claim"
                reason = reason or "no_live_mode_live_gate_not_executed"
            if status in {"skipped", "no_claim", "stale"} and reason:
                skip_reasons[gate.gate_id] = reason
            candidate = ValidationGateResult(
                gate_id=gate.gate_id,
                status=status,
                evidence_path=gate.evidence_path,
                command=gate.command,
                reason=reason,
                redacted_metadata=gate.redacted_metadata,
            )
            existing = by_id.get(gate.gate_id)
            if existing is None or _status_rank(candidate.status) > _status_rank(
                existing.status
            ):
                by_id[gate.gate_id] = candidate
    for flake in flake_records:
        if flake.scenario_id not in loaded_scenario_ids or flake.gate_id not in by_id:
            continue
        existing = by_id[flake.gate_id]
        if not flake.quarantined:
            continue
        if _flake_expired(flake, now=now):
            by_id[flake.gate_id] = ValidationGateResult(
                gate_id=existing.gate_id,
                status="failed",
                evidence_path=existing.evidence_path,
                command=existing.command,
                reason=f"quarantine_expired:{flake.quarantine_expires}",
                redacted_metadata={
                    **existing.redacted_metadata,
                    "flake_id": flake.flake_id,
                    "owner": flake.owner,
                },
            )
            continue
        by_id[flake.gate_id] = ValidationGateResult(
            gate_id=existing.gate_id,
            status="quarantined",
            evidence_path=existing.evidence_path,
            command=existing.command,
            reason=f"quarantined_until:{flake.quarantine_expires}",
            redacted_metadata={
                **existing.redacted_metadata,
                "flake_id": flake.flake_id,
                "owner": flake.owner,
            },
        )
    return [by_id[gate_id] for gate_id in sorted(by_id)], skip_reasons


def _build_regression_summary(
    validation_run: ValidationRunRecord,
    baselines: list[HarnessBaseline],
    *,
    stale_gates: list[str],
    corrupt_artifacts: list[str],
) -> RegressionSummary:
    gate_statuses = {gate.gate_id: gate.status for gate in validation_run.gate_results}
    artifact_ids = {ref.artifact_id for ref in validation_run.artifact_index_refs}
    missing_artifacts: list[str] = []
    new_failures: list[str] = []
    fixed_failures: list[str] = []
    skipped_required: list[str] = []
    no_claim_gates: list[str] = []

    for baseline in baselines:
        missing_artifacts.extend(
            artifact_id
            for artifact_id in baseline.required_artifact_ids
            if artifact_id not in artifact_ids
        )
        for gate_id, expected in baseline.expected_gate_statuses.items():
            actual = gate_statuses.get(gate_id, "not_run")
            if gate_id in _REQUIRED_DETERMINISTIC_GATE_IDS and actual in {
                "skipped",
                "not_run",
                "no_claim",
            }:
                skipped_required.append(gate_id)
            if expected == "passed" and actual != "passed":
                new_failures.append(gate_id)
            if expected != "passed" and actual == "passed":
                fixed_failures.append(gate_id)
            if actual == "no_claim":
                no_claim_gates.append(gate_id)
    candidate_status = _candidate_status(
        gate_statuses,
        new_failures=new_failures,
        skipped_required=skipped_required,
        missing_artifacts=missing_artifacts,
        corrupt_artifacts=corrupt_artifacts,
        stale_gates=stale_gates,
        no_claim_gates=no_claim_gates,
    )
    return RegressionSummary(
        summary_id=f"{validation_run.run_id}:regression",
        validation_run_id=validation_run.run_id,
        baseline_ids=[baseline.baseline_id for baseline in baselines],
        new_failures=sorted(set(new_failures)),
        fixed_failures=sorted(set(fixed_failures)),
        stale_gates=sorted(set(stale_gates)),
        missing_artifacts=sorted(set(missing_artifacts)),
        corrupt_artifacts=sorted(set(corrupt_artifacts)),
        skipped_required_gates=sorted(set(skipped_required)),
        no_claim_gates=sorted(set(no_claim_gates)),
        candidate_status=candidate_status,
    )


def _build_dashboard_summary(
    validation_run: ValidationRunRecord,
    regression: RegressionSummary,
) -> ValidationDashboardSummary:
    gate_statuses = {gate.gate_id: gate.status for gate in validation_run.gate_results}
    product_rows: list[dict[str, Any]] = []
    for pack_id in validation_run.pack_ids:
        adapter_id = _adapter_id_for_pack(pack_id)
        scenario_ids = [
            item
            for item in validation_run.scenario_ids
            if seed_scenario_specs().get(item) is not None
            and seed_scenario_specs()[item].product_adapter_id == adapter_id
        ]
        deterministic_status = _aggregate_gate_status(
            gate_statuses.get(gate_id, "not_run")
            for gate_id in _REQUIRED_DETERMINISTIC_GATE_IDS
        )
        live_status = _aggregate_gate_status(
            gate_statuses.get(gate_id, "not_run") for gate_id in _LIVE_GATE_IDS
        )
        product_rows.append(
            {
                "product_adapter_id": adapter_id,
                "pack_id": pack_id,
                "scenario_ids": scenario_ids,
                "deterministic_status": deterministic_status,
                "live_status": live_status,
            }
        )
    shared_rows = [
        {"gate_id": gate.gate_id, "status": gate.status, "reason": gate.reason}
        for gate in validation_run.gate_results
    ]
    top_failures = (
        regression.new_failures
        + regression.missing_artifacts
        + regression.corrupt_artifacts
        + regression.stale_gates
    )
    artifact_paths = [
        ref.path for ref in validation_run.artifact_index_refs if ref.path is not None
    ]
    return ValidationDashboardSummary(
        summary_id=f"{validation_run.run_id}:dashboard",
        validation_run_id=validation_run.run_id,
        candidate_status=regression.candidate_status,
        product_rows=product_rows,
        shared_rows=shared_rows,
        top_failures=top_failures[:10],
        artifact_paths=artifact_paths,
        required_followups=_required_followups(regression),
        skipped_live_gates=[
            gate.gate_id
            for gate in validation_run.gate_results
            if gate.gate_id in _LIVE_GATE_IDS and gate.status in {"skipped", "stale"}
        ],
        no_claim_gates=regression.no_claim_gates,
    )


def _stale_gates(
    loaded: list[dict[str, Any]],
    gate_results: list[ValidationGateResult],
    baselines: list[HarnessBaseline],
    *,
    now: datetime,
) -> list[str]:
    windows: dict[str, int] = {}
    for baseline in baselines:
        windows.update(baseline.gate_freshness_windows_hours)
    if not windows:
        return []
    observed_at = min(item["observed_at"] for item in loaded)
    age_hours = (now - observed_at).total_seconds() / 3600
    stale: list[str] = []
    for gate in gate_results:
        window = windows.get(gate.gate_id)
        if window is not None and gate.status == "passed" and age_hours > window:
            stale.append(gate.gate_id)
    return stale


def _candidate_status(
    gate_statuses: dict[str, str],
    *,
    new_failures: list[str],
    skipped_required: list[str],
    missing_artifacts: list[str],
    corrupt_artifacts: list[str],
    stale_gates: list[str],
    no_claim_gates: list[str],
) -> str:
    if any(status == "blocked" for status in gate_statuses.values()):
        return "blocked"
    if new_failures or skipped_required or missing_artifacts or corrupt_artifacts:
        return "failed"
    if stale_gates:
        return "stale"
    if any(status == "quarantined" for status in gate_statuses.values()):
        return "no_claim"
    if no_claim_gates:
        return "no_claim"
    return "passed"


def _required_followups(regression: RegressionSummary) -> list[str]:
    followups: list[str] = []
    if regression.new_failures:
        followups.append("Fix failing required deterministic gates.")
    if regression.missing_artifacts:
        followups.append("Attach missing evidence artifacts to the validation index.")
    if regression.corrupt_artifacts:
        followups.append("Regenerate corrupt or empty validation artifacts.")
    if regression.stale_gates:
        followups.append("Refresh stale validation evidence before release.")
    if regression.no_claim_gates:
        followups.append("Run opt-in live gates before making live/provider/UI claims.")
    return followups


def _adapter_ids_for_loaded_indexes(loaded: list[dict[str, Any]]) -> list[str]:
    return _sorted_unique(
        _adapter_id_for_pack(item["evidence_index"].pack_id)
        for item in loaded
        if isinstance(item["evidence_index"], EvidenceArtifactIndex)
    )


def _adapter_id_for_pack(pack_id: str | None) -> str:
    if pack_id == "excel_workbook_chat":
        return "excel_ai"
    if pack_id == "deep_research_chat_demo":
        return "chat_demo"
    return "unknown"


def _gate_id_for_artifact_type(artifact_type: str) -> str | None:
    return {
        "command_output": "deterministic_tests",
        "validation_gates": "support_bundle_artifact",
        "support_bundle": "support_bundle_artifact",
    }.get(artifact_type)


def _known_artifact_type(artifact_type: str) -> str:
    known = {
        "manifest",
        "evidence_index",
        "validation_gates",
        "capability_pack_resolution",
        "capability_pack_run",
        "capability_pack_dry_run",
        "command_output",
        "support_bundle",
        "trace_summary",
        "phoenix_run_ids",
        "phoenix_trace",
        "playwright_screenshot",
        "benchmark_json",
        "benchmark_markdown",
        "skip_justification",
        "validation_run_json",
        "validation_report_markdown",
        "other",
    }
    return artifact_type if artifact_type in known else "other"


def _status_rank(status: str) -> int:
    return {
        "passed": 0,
        "no_claim": 1,
        "skipped": 2,
        "not_run": 3,
        "quarantined": 4,
        "stale": 5,
        "failed": 6,
        "blocked": 7,
    }.get(status, 8)


def _aggregate_gate_status(statuses: Any) -> str:
    collected = list(statuses)
    if any(status in {"failed", "blocked"} for status in collected):
        return "failed"
    if any(status == "stale" for status in collected):
        return "stale"
    if any(status == "no_claim" for status in collected):
        return "no_claim"
    if all(status == "passed" for status in collected):
        return "passed"
    if any(status in {"skipped", "not_run"} for status in collected):
        return "skipped"
    return "unknown"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _flake_expired(flake: FlakeRecord, *, now: datetime) -> bool:
    try:
        expires = datetime.fromisoformat(flake.quarantine_expires)
    except ValueError:
        return True
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    return now > expires


def _sorted_unique(items: Any) -> list[str]:
    return sorted({str(item) for item in items if item is not None and str(item)})


__all__ = [
    "audit_validation_evidence",
    "render_validation_markdown",
    "seed_flake_records",
    "seed_harness_baselines",
    "seed_host_adoption_states",
    "seed_release_gate_policies",
    "write_validation_audit_report",
]
