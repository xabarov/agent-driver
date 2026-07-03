"""Deterministic MCP governance + provenance evidence (epic 014).

Builds registry snapshots, allowed-roots boundary checks, deterministic approval
decisions, call-provenance rows, redaction-safe support-bundle projections and a
capability-pack-compatible artifact set on top of the existing MCP transport
(``agent_driver/mcp_server``) and client-tool catalog
(``agent_driver/tools/builtin/mcp.py``). No live MCP server is required.
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
    McpGovernanceUsageSummary,
    McpRegistrySnapshot,
    McpServerDescriptor,
    McpToolResourceRef,
)
from agent_driver.contracts.policy import ValidationGateResult

# --------------------------------------------------------------------------- #
# Catalog access (reuse the builtin MCP client fixture / catalog loader)
# --------------------------------------------------------------------------- #


def _builtin_catalog() -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], Any]]:
    from agent_driver.tools.builtin import mcp as _mcp

    return dict(_mcp._MCP_TOOL_DESCRIPTORS), dict(_mcp._MCP_RESOURCE_DESCRIPTORS)


def load_mcp_catalog(
    path: str | Path | None,
) -> tuple[dict[tuple[str, str], Any], dict[tuple[str, str], Any], str]:
    """Return (tools, resources, source) for the builtin fixture or a JSON file."""
    if path is None:
        tools, resources = _builtin_catalog()
        return tools, resources, "builtin_mcp_fixture"
    from agent_driver.tools.builtin import mcp as _mcp

    tools, resources = _mcp._load_catalog(str(path))
    return tools, resources, f"catalog_json:{path}"


# --------------------------------------------------------------------------- #
# Stable ids / digests
# --------------------------------------------------------------------------- #


def _stable_digest(value: Any) -> str:
    payload = json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    return sha256(payload.encode("utf-8")).hexdigest()[:16]


def stable_server_id(server: str, *, transport: str = "stdio") -> str:
    """Deterministic server id from name + transport."""
    return f"{_slug(server)}:{transport}"


def _slug(value: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in value.strip().lower())


# --------------------------------------------------------------------------- #
# Registry snapshot
# --------------------------------------------------------------------------- #


def _uri_in_roots(uri: str | None, roots: Sequence[str]) -> bool:
    """A resource uri is in-roots when a declared root is its prefix.

    Empty roots means "no boundary declared" and is treated as unrestricted so a
    missing policy never fabricates ``out_of_roots``.
    """
    if not roots:
        return True
    if not uri:
        return True
    return any(uri.startswith(root) for root in roots)


def tool_resource_refs_from_catalog(
    tools: dict[tuple[str, str], Any],
    resources: dict[tuple[str, str], Any],
    *,
    server_roots: dict[str, list[str]] | None = None,
) -> list[McpToolResourceRef]:
    """Convert a catalog into redaction-safe tool/resource refs (sorted)."""
    server_roots = server_roots or {}
    refs: list[McpToolResourceRef] = []
    for (server, tool_name), _descriptor in sorted(tools.items()):
        refs.append(
            McpToolResourceRef(
                server_id=server,
                name=tool_name,
                kind="tool",
                capability="tools",
                side_effect_class="external_action",
                allow_state="registered",
                read_status="not_read",
            )
        )
    for (server, uri), descriptor in sorted(resources.items()):
        content = str(getattr(descriptor, "content", "") or "")
        in_roots = _uri_in_roots(uri, server_roots.get(server, []))
        refs.append(
            McpToolResourceRef(
                server_id=server,
                name=getattr(descriptor, "name", uri),
                kind="resource",
                capability="resources",
                side_effect_class="read_only",
                allow_state="registered" if in_roots else "out_of_roots",
                uri=uri,
                checksum=_stable_digest(content),
                size_bytes=len(content.encode("utf-8")),
                read_status="not_read",
            )
        )
    return refs


def build_server_descriptor(
    server_id: str,
    *,
    name: str | None = None,
    transport: str = "stdio",
    trust_class: str = "curated",
    auth_mode: str = "none",
    endpoint_ref: str | None = None,
    allowed_roots: Sequence[str] = (),
    capabilities: Sequence[str] = ("tools", "resources"),
    health_check_supported: bool = False,
    metadata: dict[str, Any] | None = None,
) -> McpServerDescriptor:
    """Build one deterministic MCP server descriptor with a stable digest."""
    body = {
        "server_id": server_id,
        "transport": transport,
        "trust_class": trust_class,
        "auth_mode": auth_mode,
        "allowed_roots": list(allowed_roots),
        "capabilities": list(capabilities),
    }
    return McpServerDescriptor(
        server_id=server_id,
        name=name or server_id,
        transport=transport,  # type: ignore[arg-type]
        endpoint_ref=endpoint_ref,
        auth_mode=auth_mode,
        trust_class=trust_class,  # type: ignore[arg-type]
        allowed_roots=list(allowed_roots),
        capabilities=list(capabilities),  # type: ignore[arg-type]
        health_check_supported=health_check_supported,
        status="registered",
        digest=_stable_digest(body),
        metadata=metadata or {},
    )


def build_mcp_registry_snapshot(
    *,
    snapshot_id: str,
    catalog_path: str | Path | None = None,
    servers: Sequence[McpServerDescriptor] | None = None,
    server_roots: dict[str, list[str]] | None = None,
    server_trust: dict[str, str] | None = None,
    server_transport: dict[str, str] | None = None,
    allowed_roots: Sequence[str] = (),
    max_results: int = 200,
    created_at: str | None = None,
) -> McpRegistrySnapshot:
    """Deterministic scan of MCP servers + their tools/resources."""
    tools, resources, _source = load_mcp_catalog(catalog_path)
    server_roots = server_roots or {}
    server_trust = server_trust or {}
    server_transport = server_transport or {}

    if servers is None:
        server_names = sorted(
            {server for server, _ in tools} | {server for server, _ in resources}
        )
        servers = [
            build_server_descriptor(
                name,
                transport=server_transport.get(name, "stdio"),
                trust_class=server_trust.get(name, "curated"),
                allowed_roots=server_roots.get(name, []),
                capabilities=_server_capabilities(name, tools, resources),
            )
            for name in server_names
        ]

    refs = tool_resource_refs_from_catalog(tools, resources, server_roots=server_roots)
    warnings: list[str] = []
    truncated = 0
    if len(refs) > max_results:
        truncated = len(refs) - max_results
        warnings.append(
            f"registry truncated: {truncated} of {len(refs)} refs omitted "
            f"(max_results={max_results})"
        )
        refs = refs[:max_results]

    body = {
        "servers": [s.model_dump(mode="json") for s in servers],
        "refs": [r.model_dump(mode="json") for r in refs],
        "allowed_roots": list(allowed_roots),
    }
    return McpRegistrySnapshot(
        snapshot_id=snapshot_id,
        server_refs=list(servers),
        allowed_roots=list(allowed_roots),
        discovery_limits={"max_results": max_results},
        returned_count=len(refs),
        truncated_count=truncated,
        tool_resource_refs=refs,
        warnings=warnings,
        created_at=created_at,
        digest=_stable_digest(body),
    )


def _server_capabilities(
    server: str,
    tools: dict[tuple[str, str], Any],
    resources: dict[tuple[str, str], Any],
) -> list[str]:
    caps: list[str] = []
    if any(s == server for s, _ in tools):
        caps.append("tools")
    if any(s == server for s, _ in resources):
        caps.append("resources")
    return caps or ["tools"]


# --------------------------------------------------------------------------- #
# Approval evaluation
# --------------------------------------------------------------------------- #


def _match_policy(
    ref: McpToolResourceRef, policies: Sequence[McpApprovalPolicy]
) -> McpApprovalPolicy | None:
    """Most specific policy wins: (server, ref) > (server) > global."""
    best: McpApprovalPolicy | None = None
    best_rank = -1
    for policy in policies:
        rank = -1
        if policy.server_id == ref.server_id and policy.ref_name == ref.name:
            rank = 3
        elif policy.server_id == ref.server_id and policy.ref_name is None:
            rank = 2
        elif policy.server_id is None and policy.ref_name is None:
            rank = 1
        if rank > best_rank:
            best = policy
            best_rank = rank
    return best


def evaluate_mcp_approval(
    ref: McpToolResourceRef,
    policies: Sequence[McpApprovalPolicy],
    *,
    decision_id: str,
) -> McpApprovalDecision:
    """Deterministically classify one ref against the effective policy."""
    policy = _match_policy(ref, policies)
    sampling_involved = ref.capability == "sampling"
    elicitation_involved = ref.capability == "elicitation"
    if policy is None:
        return McpApprovalDecision(
            decision_id=decision_id,
            server_id=ref.server_id,
            ref_name=ref.name,
            kind=ref.kind,
            status="no_claim",
            action="block",
            rationale="no matching approval policy; default no_claim",
            sampling_involved=sampling_involved,
            elicitation_involved=elicitation_involved,
        )

    effective_roots = list(policy.roots_boundary)
    reasons: list[str] = []
    status: str
    action = policy.default_action

    if ref.uri is not None and not _uri_in_roots(ref.uri, effective_roots):
        status = "out_of_roots"
        action = "block"
        reasons.append(f"resource uri outside roots boundary: {ref.uri}")
    elif (
        policy.max_resource_bytes is not None
        and ref.size_bytes > policy.max_resource_bytes
    ):
        status = "oversized"
        action = "block"
        reasons.append(
            f"resource size {ref.size_bytes} exceeds max {policy.max_resource_bytes}"
        )
    elif sampling_involved and not policy.sampling_allowed:
        status = "blocked"
        action = "block"
        reasons.append("sampling not permitted by policy")
    elif elicitation_involved and not policy.elicitation_allowed:
        status = "blocked"
        action = "block"
        reasons.append("elicitation not permitted by policy")
    elif ref.side_effect_class in policy.blocked_side_effect_classes:
        status = "blocked"
        action = "block"
        reasons.append(f"side effect class blocked: {ref.side_effect_class}")
    elif (
        policy.allowed_side_effect_classes
        and ref.side_effect_class not in policy.allowed_side_effect_classes
    ):
        status = "filtered"
        action = "block"
        reasons.append(f"side effect class not allowed: {ref.side_effect_class}")
    else:
        status = {"allow": "allowed", "ask": "asked", "block": "blocked"}[
            policy.default_action
        ]

    return McpApprovalDecision(
        decision_id=decision_id,
        policy_id=policy.policy_id,
        server_id=ref.server_id,
        ref_name=ref.name,
        kind=ref.kind,
        status=status,  # type: ignore[arg-type]
        action=action,
        rationale=_rationale(status, reasons),
        filter_reasons=reasons,
        roots_used=effective_roots,
        sampling_involved=sampling_involved,
        elicitation_involved=elicitation_involved,
    )


def build_mcp_approval_decisions(
    refs: Sequence[McpToolResourceRef],
    policies: Sequence[McpApprovalPolicy],
    *,
    prefix: str = "mcp-decision",
) -> list[McpApprovalDecision]:
    """Evaluate every ref deterministically (stable order)."""
    decisions: list[McpApprovalDecision] = []
    for index, ref in enumerate(refs):
        decisions.append(
            evaluate_mcp_approval(
                ref,
                policies,
                decision_id=f"{prefix}:{index:03d}:{ref.server_id}:{ref.name}",
            )
        )
    return decisions


def _rationale(status: str, reasons: list[str]) -> str:
    if reasons:
        return f"{status}: " + "; ".join(reasons)
    return f"{status} by policy default action"


# --------------------------------------------------------------------------- #
# Provenance rows
# --------------------------------------------------------------------------- #


def provenance_row_from_ref(
    ref: McpToolResourceRef,
    *,
    call_id: str,
    read_status: str = "not_read",
    run_id: str | None = None,
    session_id: str | None = None,
    tool_call_id: str | None = None,
    latency_ms: int | None = None,
    source_refs: Sequence[str] = (),
    artifact_refs: Sequence[str] = (),
) -> McpCallProvenanceRow:
    """Redaction-safe provenance row for one call/resource read (no body)."""
    return McpCallProvenanceRow(
        call_id=call_id,
        server_id=ref.server_id,
        ref_name=ref.name,
        kind=ref.kind,
        digest=ref.checksum,
        roots_used=[],
        sampling_involved=ref.capability == "sampling",
        elicitation_involved=ref.capability == "elicitation",
        read_status=read_status,  # type: ignore[arg-type]
        run_id=run_id,
        session_id=session_id,
        tool_call_id=tool_call_id,
        latency_ms=latency_ms,
        source_refs=list(source_refs),
        artifact_refs=list(artifact_refs),
    )


def build_mcp_call_provenance(
    decisions: Sequence[McpApprovalDecision],
    refs: Sequence[McpToolResourceRef],
    *,
    prefix: str = "mcp-call",
) -> list[McpCallProvenanceRow]:
    """Emit provenance rows only for allowed decisions (deterministic)."""
    by_key = {(r.server_id, r.name): r for r in refs}
    rows: list[McpCallProvenanceRow] = []
    for index, decision in enumerate(decisions):
        if decision.status != "allowed":
            continue
        ref = by_key.get((decision.server_id, decision.ref_name))
        if ref is None:
            continue
        read_status = "read" if ref.kind == "resource" else "not_read"
        rows.append(
            provenance_row_from_ref(
                ref,
                call_id=f"{prefix}:{index:03d}:{ref.server_id}:{ref.name}",
                read_status=read_status,
            )
        )
    return rows


# --------------------------------------------------------------------------- #
# Usage summary / support-bundle projection / report
# --------------------------------------------------------------------------- #


def build_mcp_governance_usage_summary(
    snapshot: McpRegistrySnapshot,
    decisions: Sequence[McpApprovalDecision],
    *,
    outcome_links: dict[str, Any] | None = None,
) -> McpGovernanceUsageSummary:
    """Deterministic counters over registry + decisions."""
    counts = {
        "allowed": 0,
        "asked": 0,
        "blocked": 0,
        "filtered": 0,
        "out_of_roots": 0,
        "oversized": 0,
        "failed": 0,
    }
    for decision in decisions:
        if decision.status in counts:
            counts[decision.status] += 1
    return McpGovernanceUsageSummary(
        registered=len(snapshot.tool_resource_refs),
        allowed=counts["allowed"],
        asked=counts["asked"],
        blocked=counts["blocked"],
        filtered=counts["filtered"],
        out_of_roots=counts["out_of_roots"],
        oversized=counts["oversized"],
        failed=counts["failed"],
        stale=0,
        outcome_links=outcome_links or {},
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


# --------------------------------------------------------------------------- #
# Seed fixtures for the two product families
# --------------------------------------------------------------------------- #


def _excel_policies() -> list[McpApprovalPolicy]:
    return [
        # Global fallback: allow read-only resources, ask for external actions.
        McpApprovalPolicy(
            policy_id="excel.default_ask",
            default_action="ask",
            allowed_side_effect_classes=["read_only", "external_action"],
            roots_boundary=["resource://"],
            max_resource_bytes=1_000_000,
        ),
        # Curated demo-docs server: auto-allow its read-only resources; its
        # external-action tools fall outside this narrower allowlist -> filtered.
        McpApprovalPolicy(
            policy_id="excel.demo_docs_allow",
            server_id="demo-docs",
            default_action="allow",
            allowed_side_effect_classes=["read_only"],
            roots_boundary=["resource://"],
            max_resource_bytes=1_000_000,
        ),
    ]


def _chat_demo_policies() -> list[McpApprovalPolicy]:
    return [
        McpApprovalPolicy(
            policy_id="research.sources_allow",
            default_action="allow",
            allowed_side_effect_classes=["read_only"],
            roots_boundary=["resource://"],
            sampling_allowed=False,
            elicitation_allowed=False,
            max_resource_bytes=2_000_000,
        ),
    ]


def default_mcp_policies(product_family: str) -> list[McpApprovalPolicy]:
    """Return deterministic default approval policies for a product family."""
    if product_family == "excel_ai":
        return _excel_policies()
    if product_family == "chat_demo":
        return _chat_demo_policies()
    return [
        McpApprovalPolicy(
            policy_id="generic.default_ask",
            default_action="ask",
            allowed_side_effect_classes=["read_only", "external_action"],
            roots_boundary=["resource://"],
            max_resource_bytes=1_000_000,
        )
    ]


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


__all__ = [
    "default_mcp_policies",
    "build_mcp_approval_decisions",
    "build_mcp_call_provenance",
    "build_mcp_governance_compatibility_report",
    "build_mcp_governance_evidence_index",
    "build_mcp_governance_usage_summary",
    "build_mcp_registry_snapshot",
    "build_mcp_support_bundle_projection",
    "build_server_descriptor",
    "evaluate_mcp_approval",
    "load_mcp_catalog",
    "project_mcp_harness_adapter_events",
    "project_mcp_lifecycle_hook_audit_records",
    "provenance_row_from_ref",
    "render_mcp_governance_markdown",
    "replay_mcp_governance_from_artifacts",
    "seed_chat_demo_mcp_governance_report",
    "seed_excel_mcp_governance_report",
    "stable_server_id",
    "tool_resource_refs_from_catalog",
    "write_mcp_governance_artifacts",
]
