"""Skill-lifecycle evidence index, markdown render, artifact write + replay.

Extracted verbatim from ``skills/lifecycle`` (god-module split, behaviour-neutral).
Self-contained: external contracts + the shared product helpers from
``lifecycle_common``; re-exported from ``lifecycle`` for existing callers/tests.
"""

from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any

from agent_driver.contracts.capability_packs import (
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.contracts.skills_lifecycle import (
    SkillInventorySnapshot,
    SkillLifecycleCompatibilityReport,
    SkillLockFile,
)
from agent_driver.skills.lifecycle_common import (
    _pack_id_for_product,
    _primary_skill_scenario,
)


def replay_skill_lifecycle_from_artifacts(
    root: str | Path,
) -> SkillLifecycleCompatibilityReport:
    """Replay a skill report from persisted deterministic artifacts."""
    path = Path(root)
    report_path = path / "skills_compatibility_report.json"
    if not report_path.is_file():
        raise FileNotFoundError(f"missing skills_compatibility_report.json: {path}")
    return SkillLifecycleCompatibilityReport.model_validate_json(
        report_path.read_text(encoding="utf-8")
    )


def build_skill_lifecycle_evidence_index(
    report: SkillLifecycleCompatibilityReport,
    *,
    pack_id: str | None = None,
    scenario_ids: list[str] | None = None,
    include_live_no_claim_gates: bool = True,
) -> EvidenceArtifactIndex:
    """Build deterministic evidence index rows for 008 validation audit."""
    scenarios = scenario_ids or [
        "skills_lifecycle.inventory_lock_diff.v1",
        "skills_lifecycle.selection_evidence.v1",
        "skills_lifecycle.invocation_provenance.v1",
        _primary_skill_scenario(report.product_family),
    ]
    gates = [
        ValidationGateResult(
            gate_id="deterministic_tests",
            status="passed",
            evidence_path="skills_compatibility_report.json",
            redacted_metadata={"scenario": "skills_lifecycle"},
        ),
        ValidationGateResult(
            gate_id="support_bundle_artifact",
            status="passed",
            evidence_path="skills_compatibility_report.md",
            redacted_metadata={"scenario": "skills_lifecycle"},
        ),
    ]
    if include_live_no_claim_gates:
        gates.extend(
            [
                ValidationGateResult(
                    gate_id="phoenix_trace",
                    status="no_claim",
                    reason="skills_lifecycle_no_live_mode",
                ),
                ValidationGateResult(
                    gate_id="playwright_ui",
                    status="no_claim",
                    reason="skills_lifecycle_no_ui_change",
                ),
                ValidationGateResult(
                    gate_id="benchmark_delta",
                    status="no_claim",
                    reason="skills_lifecycle_no_quality_claim",
                ),
            ]
        )
    return EvidenceArtifactIndex(
        index_id=f"{report.report_id}:evidence",
        pack_id=pack_id or _pack_id_for_product(report.product_family),
        scenario_ids=[item for item in scenarios if item],
        gates=gates,
        artifacts=[
            EvidenceArtifactRef(
                artifact_id="skills_inventory_snapshot.json",
                artifact_type="skill_inventory_snapshot",
                path="skills_inventory_snapshot.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_lock.json",
                artifact_type="skill_lockfile",
                path="skills_lock.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_reload_diff.json",
                artifact_type="skill_reload_diff",
                path="skills_reload_diff.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_selection_decisions.json",
                artifact_type="skill_selection_evidence",
                path="skills_selection_decisions.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_compatibility_report.json",
                artifact_type="skill_lifecycle_compatibility_report",
                path="skills_compatibility_report.json",
                gate_id="support_bundle_artifact",
            ),
            EvidenceArtifactRef(
                artifact_id="skills_compatibility_report.md",
                artifact_type="skill_lifecycle_compatibility_report",
                path="skills_compatibility_report.md",
                gate_id="support_bundle_artifact",
            ),
            EvidenceArtifactRef(
                artifact_id="validation_gates.json",
                artifact_type="validation_gates",
                path="validation_gates.json",
                gate_id="support_bundle_artifact",
            ),
        ],
        skipped_gate_ids=[
            gate.gate_id for gate in gates if gate.status in {"no_claim", "skipped"}
        ],
    )


def render_skill_lifecycle_markdown(
    report: SkillLifecycleCompatibilityReport,
) -> str:
    """Render a compact skill lifecycle report for owner handoff."""
    lines = [
        f"# Skills Lifecycle Compatibility {report.report_id}",
        "",
        f"Product family: `{report.product_family}`",
        f"Host profile: `{report.host_profile}`",
        f"Inventory snapshot: `{report.inventory_snapshot_id or 'none'}`",
        f"Lockfile: `{report.lock_id or 'none'}`",
        "",
        "## Usage",
        "",
        "| Counter | Value |",
        "|---|---:|",
    ]
    usage = report.usage_summary.model_dump(mode="json")
    for key in (
        "discovered",
        "selected",
        "viewed",
        "supporting_file_viewed",
        "filtered",
        "blocked",
        "failed",
        "stale",
    ):
        lines.append(f"| {key} | {usage[key]} |")
    lines.extend(
        [
            "",
            "## Selection Decisions",
            "",
            "| Skill | Status | Reason |",
            "|---|---|---|",
        ]
    )
    for decision in report.selections_made:
        lines.append(
            f"| {decision.name or decision.skill_id or 'none'} | "
            f"{decision.status} | {decision.rationale} |"
        )
    if not report.selections_made:
        lines.append("| none | no_claim | no deterministic selections |")
    lines.extend(["", "## No-Claim States", ""])
    for item in report.no_claims:
        lines.append(f"- {item}")
    if not report.no_claims:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)


def write_skill_lifecycle_artifacts(
    output_dir: str | Path,
    *,
    snapshot: SkillInventorySnapshot,
    lockfile: SkillLockFile,
    diff: SkillReloadDiff,
    report: SkillLifecycleCompatibilityReport,
    evidence_index: EvidenceArtifactIndex | None = None,
) -> dict[str, str]:
    """Persist skill lifecycle JSON/Markdown artifacts and manifest."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    index = evidence_index or build_skill_lifecycle_evidence_index(report)
    payloads: list[tuple[str, str, dict[str, Any] | str]] = [
        (
            "skills_inventory_snapshot.json",
            "skill_inventory_snapshot",
            snapshot.model_dump(mode="json"),
        ),
        ("skills_lock.json", "skill_lockfile", lockfile.model_dump(mode="json")),
        ("skills_reload_diff.json", "skill_reload_diff", diff.model_dump(mode="json")),
        (
            "skills_selection_decisions.json",
            "skill_selection_evidence",
            {
                "decisions": [
                    item.model_dump(mode="json") for item in report.selections_made
                ]
            },
        ),
        (
            "skills_invocation_records.json",
            "skill_selection_evidence",
            {
                "invocations": [
                    item.model_dump(mode="json") for item in report.invocations_recorded
                ]
            },
        ),
        (
            "skills_compatibility_report.json",
            "skill_lifecycle_compatibility_report",
            report.model_dump(mode="json"),
        ),
        (
            "skills_compatibility_report.md",
            "skill_lifecycle_compatibility_report",
            render_skill_lifecycle_markdown(report),
        ),
        (
            "validation_gates.json",
            "validation_gates",
            _validation_gate_summary(index),
        ),
        ("evidence_index.json", "evidence_index", index.model_dump(mode="json")),
    ]
    artifacts: list[dict[str, Any]] = []
    for filename, artifact_type, payload in payloads:
        path = root / filename
        if isinstance(payload, str):
            path.write_text(payload, encoding="utf-8")
        else:
            path.write_text(
                json.dumps(payload, ensure_ascii=True, indent=2) + "\n",
                encoding="utf-8",
            )
        artifacts.append(_artifact_manifest_row(path, artifact_type, root=root))
    manifest = {
        "artifact_count": len(artifacts),
        "artifacts": artifacts,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_skill_body": False,
            "contains_raw_supporting_files": False,
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        Path(item["path"]).stem: str(root / str(item["path"])) for item in artifacts
    }


def _artifact_manifest_row(
    path: Path,
    artifact_type: str,
    *,
    root: Path,
) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact_type": artifact_type,
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(data),
        "sha256": sha256(data).hexdigest(),
    }


def _validation_gate_summary(index: EvidenceArtifactIndex) -> dict[str, Any]:
    gates = [gate.model_dump(mode="json") for gate in index.gates]
    return {
        "count": len(gates),
        "statuses": {gate["gate_id"]: gate["status"] for gate in gates},
        "gates": gates,
        "redaction": {"safe_by_default": True},
    }


__all__ = [
    "replay_skill_lifecycle_from_artifacts",
    "build_skill_lifecycle_evidence_index",
    "render_skill_lifecycle_markdown",
    "write_skill_lifecycle_artifacts",
]
