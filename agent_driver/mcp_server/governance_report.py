"""MCP governance reporting: support-bundle projection, compatibility report,
evidence index, markdown render, artifact write/replay, and deterministic seeds.

Split out of ``mcp_server/governance.py`` (god-module split, behaviour-neutral);
re-exported from ``governance`` for existing callers.
"""


from __future__ import annotations

import json
from hashlib import sha256
from pathlib import Path
from typing import Any, Sequence

from agent_driver.contracts.capability_packs import (
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
)
from agent_driver.contracts.mcp_governance import (
    McpApprovalDecision,
    McpApprovalPolicy,
    McpCallProvenanceRow,
    McpGovernanceCompatibilityReport,
    McpRegistrySnapshot,
)
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.mcp_server.governance_core import (
    _chat_demo_policies,
    _excel_policies,
    build_mcp_approval_decisions,
    build_mcp_call_provenance,
    build_mcp_governance_usage_summary,
    build_mcp_registry_snapshot,
)


def build_mcp_support_bundle_projection(
    snapshot: McpRegistrySnapshot,
    decisions: Sequence[McpApprovalDecision],
) -> list[dict[str, Any]]:
    """Compact, credential-free support-bundle rows for adapters."""
    rows: list[dict[str, Any]] = []
    for server in snapshot.server_refs:
        rows.append(
            {
                "kind": "mcp_server",
                "server_id": server.server_id,
                "transport": server.transport,
                "trust_class": server.trust_class,
                "auth_mode": server.auth_mode,
                "digest": server.digest,
                "capabilities": list(server.capabilities),
            }
        )
    for decision in decisions:
        rows.append(
            {
                "kind": "mcp_decision",
                "server_id": decision.server_id,
                "ref_name": decision.ref_name,
                "ref_kind": decision.kind,
                "status": decision.status,
                "action": decision.action,
                "reasons": list(decision.filter_reasons),
            }
        )
    return rows

def build_mcp_governance_compatibility_report(
    *,
    report_id: str,
    product_family: str,
    host_profile: str,
    snapshot: McpRegistrySnapshot,
    policies_applied: Sequence[McpApprovalPolicy],
    approvals_recorded: Sequence[McpApprovalDecision],
    provenance_rows: Sequence[McpCallProvenanceRow] = (),
    no_claims: Sequence[str] | None = None,
    evidence_refs: Sequence[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> McpGovernanceCompatibilityReport:
    """Assemble the host/product MCP governance compatibility report."""
    usage = build_mcp_governance_usage_summary(snapshot, approvals_recorded)
    projection = build_mcp_support_bundle_projection(snapshot, approvals_recorded)
    return McpGovernanceCompatibilityReport(
        report_id=report_id,
        product_family=product_family,
        host_profile=host_profile,
        registry_snapshot_id=snapshot.snapshot_id,
        roots_allowed=list(snapshot.allowed_roots),
        servers_registered=[s.server_id for s in snapshot.server_refs],
        policies_applied=list(policies_applied),
        approvals_recorded=list(approvals_recorded),
        provenance_rows=list(provenance_rows),
        usage_summary=usage,
        no_claims=list(
            no_claims
            or [
                "live MCP/provider evidence is no_claim unless a probe is captured",
                "UI MCP rows require Playwright evidence if changed",
                "quality/cost/latency claims require benchmark artifacts",
            ]
        ),
        support_bundle_projection=projection,
        evidence_refs=list(evidence_refs or []),
        metadata=metadata or {},
    )

# --------------------------------------------------------------------------- #
# Lifecycle-hook / adapter projections (compact, opt-in)
# --------------------------------------------------------------------------- #


def project_mcp_lifecycle_hook_audit_records(
    report: McpGovernanceCompatibilityReport,
) -> list[dict[str, Any]]:
    """Compact audit rows for the 010 lifecycle-hook plane."""
    return [
        {
            "event_type": "pre_tool_use",
            "hook_id": "mcp_governance",
            "verdict": _verdict_for_status(decision.status),
            "server_id": decision.server_id,
            "ref_name": decision.ref_name,
            "status": decision.status,
        }
        for decision in report.approvals_recorded
    ]

def project_mcp_harness_adapter_events(
    report: McpGovernanceCompatibilityReport,
) -> list[dict[str, Any]]:
    """Compact adapter events for the 009 harness-adapter plane."""
    return [
        {
            "type": "mcp_approval",
            "server_id": decision.server_id,
            "ref_name": decision.ref_name,
            "status": decision.status,
            "action": decision.action,
        }
        for decision in report.approvals_recorded
    ]

def _verdict_for_status(status: str) -> str:
    if status == "allowed":
        return "allow"
    if status == "asked":
        return "ask"
    return "block"

# --------------------------------------------------------------------------- #
# Evidence index + artifacts (capability-pack compatible)
# --------------------------------------------------------------------------- #


def build_mcp_governance_evidence_index(
    report: McpGovernanceCompatibilityReport,
    *,
    pack_id: str | None = None,
    scenario_ids: list[str] | None = None,
    include_live_no_claim_gates: bool = True,
) -> EvidenceArtifactIndex:
    """Build deterministic evidence index rows for the 008 validation audit."""
    scenarios = scenario_ids or [
        "mcp_governance.registry_roots.v1",
        "mcp_governance.approval_policy.v1",
        "mcp_governance.call_provenance.v1",
        _primary_mcp_scenario(report.product_family),
    ]
    gates = [
        ValidationGateResult(
            gate_id="deterministic_tests",
            status="passed",
            evidence_path="mcp_governance_report.json",
            redacted_metadata={"scenario": "mcp_governance"},
        ),
        ValidationGateResult(
            gate_id="support_bundle_artifact",
            status="passed",
            evidence_path="mcp_governance_report.md",
            redacted_metadata={"scenario": "mcp_governance"},
        ),
    ]
    if include_live_no_claim_gates:
        gates.extend(
            [
                ValidationGateResult(
                    gate_id="live_mcp_probe",
                    status="no_claim",
                    reason="mcp_governance_no_live_mode",
                ),
                ValidationGateResult(
                    gate_id="phoenix_trace",
                    status="no_claim",
                    reason="mcp_governance_no_live_mode",
                ),
                ValidationGateResult(
                    gate_id="playwright_ui",
                    status="no_claim",
                    reason="mcp_governance_no_ui_change",
                ),
                ValidationGateResult(
                    gate_id="benchmark_delta",
                    status="no_claim",
                    reason="mcp_governance_no_quality_claim",
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
                artifact_id="mcp_registry_snapshot.json",
                artifact_type="mcp_registry_snapshot",
                path="mcp_registry_snapshot.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="mcp_approval_decisions.json",
                artifact_type="mcp_approval_evidence",
                path="mcp_approval_decisions.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="mcp_call_provenance.json",
                artifact_type="mcp_call_provenance",
                path="mcp_call_provenance.json",
                gate_id="deterministic_tests",
            ),
            EvidenceArtifactRef(
                artifact_id="mcp_governance_report.json",
                artifact_type="mcp_governance_compatibility_report",
                path="mcp_governance_report.json",
                gate_id="support_bundle_artifact",
            ),
            EvidenceArtifactRef(
                artifact_id="mcp_governance_report.md",
                artifact_type="mcp_governance_compatibility_report",
                path="mcp_governance_report.md",
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

def render_mcp_governance_markdown(
    report: McpGovernanceCompatibilityReport,
) -> str:
    """Render a compact MCP governance report for owner handoff."""
    lines = [
        f"# MCP Governance Compatibility {report.report_id}",
        "",
        f"Product family: `{report.product_family}`",
        f"Host profile: `{report.host_profile}`",
        f"Registry snapshot: `{report.registry_snapshot_id or 'none'}`",
        f"Servers: `{', '.join(report.servers_registered) or 'none'}`",
        "",
        "## Usage",
        "",
        "| Counter | Value |",
        "|---|---:|",
    ]
    usage = report.usage_summary.model_dump(mode="json")
    for key in (
        "registered",
        "allowed",
        "asked",
        "blocked",
        "filtered",
        "out_of_roots",
        "oversized",
        "failed",
    ):
        lines.append(f"| {key} | {usage[key]} |")
    lines.extend(
        [
            "",
            "## Approval Decisions",
            "",
            "| Server | Ref | Status | Action | Reason |",
            "|---|---|---|---|---|",
        ]
    )
    for decision in report.approvals_recorded:
        reason = "; ".join(decision.filter_reasons) or decision.rationale
        lines.append(
            f"| {decision.server_id} | {decision.ref_name} | {decision.status} "
            f"| {decision.action} | {reason} |"
        )
    if not report.approvals_recorded:
        lines.append("| none | none | no_claim | block | no deterministic decisions |")
    lines.extend(["", "## No-Claim States", ""])
    for item in report.no_claims:
        lines.append(f"- {item}")
    if not report.no_claims:
        lines.append("- none")
    lines.append("")
    return "\n".join(lines)

def write_mcp_governance_artifacts(
    output_dir: str | Path,
    *,
    snapshot: McpRegistrySnapshot,
    decisions: Sequence[McpApprovalDecision],
    provenance: Sequence[McpCallProvenanceRow],
    report: McpGovernanceCompatibilityReport,
    evidence_index: EvidenceArtifactIndex | None = None,
) -> dict[str, str]:
    """Persist MCP governance JSON/Markdown artifacts and manifest."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    index = evidence_index or build_mcp_governance_evidence_index(report)
    payloads: list[tuple[str, str, dict[str, Any] | str]] = [
        (
            "mcp_registry_snapshot.json",
            "mcp_registry_snapshot",
            snapshot.model_dump(mode="json"),
        ),
        (
            "mcp_approval_decisions.json",
            "mcp_approval_evidence",
            {"decisions": [d.model_dump(mode="json") for d in decisions]},
        ),
        (
            "mcp_call_provenance.json",
            "mcp_call_provenance",
            {"provenance": [p.model_dump(mode="json") for p in provenance]},
        ),
        (
            "mcp_governance_report.json",
            "mcp_governance_compatibility_report",
            report.model_dump(mode="json"),
        ),
        (
            "mcp_governance_report.md",
            "mcp_governance_compatibility_report",
            render_mcp_governance_markdown(report),
        ),
        ("validation_gates.json", "validation_gates", _validation_gate_summary(index)),
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
            "contains_raw_resource_body": False,
            "contains_credentials": False,
        },
    }
    (root / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return {
        Path(item["path"]).stem: str(root / str(item["path"])) for item in artifacts
    }

def replay_mcp_governance_from_artifacts(
    output_dir: str | Path,
) -> McpGovernanceCompatibilityReport:
    """Rebuild the report from persisted artifacts (replay stability check)."""
    root = Path(output_dir)
    payload = json.loads((root / "mcp_governance_report.json").read_text("utf-8"))
    return McpGovernanceCompatibilityReport.model_validate(payload)

def seed_excel_mcp_governance_report() -> McpGovernanceCompatibilityReport:
    """Deterministic Excel AI MCP governance fixture."""
    snapshot = build_mcp_registry_snapshot(
        snapshot_id="excel_ai-mcp-registry",
        server_roots={
            "demo-docs": ["resource://docs/"],
            "demo-ops": ["resource://jobs/"],
        },
        server_trust={"demo-docs": "curated", "demo-ops": "host_bundle"},
        allowed_roots=["resource://docs/", "resource://jobs/"],
    )
    policies = _excel_policies()
    decisions = build_mcp_approval_decisions(snapshot.tool_resource_refs, policies)
    provenance = build_mcp_call_provenance(decisions, snapshot.tool_resource_refs)
    return build_mcp_governance_compatibility_report(
        report_id="mcp-governance:excel_ai",
        product_family="excel_ai",
        host_profile="excel_ai",
        snapshot=snapshot,
        policies_applied=policies,
        approvals_recorded=decisions,
        provenance_rows=provenance,
        evidence_refs=[
            "mcp_registry_snapshot.json",
            "mcp_approval_decisions.json",
            "mcp_call_provenance.json",
            "mcp_governance_report.json",
            "mcp_governance_report.md",
        ],
        metadata={"scenario": "mcp_governance.excel_connectors.v1"},
    )

def seed_chat_demo_mcp_governance_report() -> McpGovernanceCompatibilityReport:
    """Deterministic chat-demo deep research MCP governance fixture."""
    snapshot = build_mcp_registry_snapshot(
        snapshot_id="chat_demo-mcp-registry",
        server_roots={
            "demo-docs": ["resource://docs/"],
            "demo-ops": ["resource://jobs/"],
        },
        server_trust={"demo-docs": "curated", "demo-ops": "external"},
        allowed_roots=["resource://docs/"],
    )
    policies = _chat_demo_policies()
    decisions = build_mcp_approval_decisions(snapshot.tool_resource_refs, policies)
    provenance = build_mcp_call_provenance(decisions, snapshot.tool_resource_refs)
    return build_mcp_governance_compatibility_report(
        report_id="mcp-governance:chat_demo",
        product_family="chat_demo",
        host_profile="chat_demo",
        snapshot=snapshot,
        policies_applied=policies,
        approvals_recorded=decisions,
        provenance_rows=provenance,
        evidence_refs=[
            "mcp_registry_snapshot.json",
            "mcp_governance_report.json",
        ],
        metadata={"scenario": "mcp_governance.chat_demo_research.v1"},
    )

# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #


def _artifact_manifest_row(
    path: Path, artifact_type: str, *, root: Path
) -> dict[str, Any]:
    data = path.read_bytes()
    return {
        "artifact_type": artifact_type,
        "path": path.relative_to(root).as_posix(),
        "size_bytes": len(data),
        "sha256": sha256(data).hexdigest(),
    }

def _primary_mcp_scenario(product_family: str) -> str:
    if product_family == "excel_ai":
        return "mcp_governance.excel_connectors.v1"
    if product_family == "chat_demo":
        return "mcp_governance.chat_demo_research.v1"
    return "mcp_governance.approval_policy.v1"

def _pack_id_for_product(product_family: str) -> str | None:
    if product_family == "excel_ai":
        return "excel_workbook_chat"
    if product_family == "chat_demo":
        return "deep_research_chat_demo"
    return None

def _validation_gate_summary(index: EvidenceArtifactIndex) -> dict[str, Any]:
    gates = [gate.model_dump(mode="json") for gate in index.gates]
    return {
        "count": len(gates),
        "statuses": {gate["gate_id"]: gate["status"] for gate in gates},
        "gates": gates,
        "redaction": {"safe_by_default": True},
    }
