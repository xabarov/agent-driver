"""Tests for deterministic MCP governance evidence (epic 014)."""

from __future__ import annotations

import argparse

import pytest
from pydantic import ValidationError

from agent_driver.contracts.mcp_governance import (
    McpApprovalDecision,
    McpApprovalPolicy,
    McpServerDescriptor,
    McpToolResourceRef,
)
from agent_driver.harness import audit_validation_evidence
from agent_driver.cli.commands.mcp_governance import mcp_governance_audit_command
from agent_driver.mcp_server.governance import (
    build_mcp_approval_decisions,
    build_mcp_call_provenance,
    build_mcp_governance_evidence_index,
    build_mcp_registry_snapshot,
    default_mcp_policies,
    evaluate_mcp_approval,
    project_mcp_harness_adapter_events,
    project_mcp_lifecycle_hook_audit_records,
    render_mcp_governance_markdown,
    replay_mcp_governance_from_artifacts,
    seed_chat_demo_mcp_governance_report,
    seed_excel_mcp_governance_report,
    write_mcp_governance_artifacts,
)


def _ref(
    name: str,
    *,
    kind: str = "resource",
    side_effect: str = "read_only",
    uri: str | None = "resource://docs/x",
    size: int = 10,
    capability: str = "resources",
) -> McpToolResourceRef:
    return McpToolResourceRef(
        server_id="demo",
        name=name,
        kind=kind,  # type: ignore[arg-type]
        capability=capability,  # type: ignore[arg-type]
        side_effect_class=side_effect,
        uri=uri,
        size_bytes=size,
    )


def test_registry_snapshot_builds_refs_marks_out_of_roots_and_truncates() -> None:
    snapshot = build_mcp_registry_snapshot(
        snapshot_id="reg",
        server_roots={
            "demo-docs": ["resource://docs/"],
            "demo-ops": ["resource://jobs/"],
        },
        allowed_roots=["resource://docs/", "resource://jobs/"],
    )
    assert snapshot.snapshot_id == "reg"
    assert {s.server_id for s in snapshot.server_refs} == {"demo-docs", "demo-ops"}
    # Every builtin resource lives under its declared root here.
    resources = [r for r in snapshot.tool_resource_refs if r.kind == "resource"]
    assert resources and all(r.allow_state == "registered" for r in resources)
    assert snapshot.digest  # deterministic digest present

    # A too-tight root marks the resource out_of_roots on the ref itself.
    tight = build_mcp_registry_snapshot(
        snapshot_id="reg2",
        server_roots={"demo-docs": ["resource://nope/"]},
    )
    docs = [
        r
        for r in tight.tool_resource_refs
        if r.server_id == "demo-docs" and r.kind == "resource"
    ]
    assert docs and all(r.allow_state == "out_of_roots" for r in docs)

    # Truncation is reported, not silent.
    truncated = build_mcp_registry_snapshot(snapshot_id="reg3", max_results=1)
    assert truncated.returned_count == 1
    assert truncated.truncated_count >= 1
    assert any("truncated" in w for w in truncated.warnings)


def test_contracts_reject_secrets_raw_bodies_and_duplicate_server_ids() -> None:
    with pytest.raises(ValidationError):
        McpServerDescriptor(
            server_id="s", name="s", digest="d", metadata={"api_key": "sk-real-secret"}
        )
    with pytest.raises(ValidationError):
        McpToolResourceRef(server_id="s", name="r", metadata={"resource_body": "raw"})
    # auth_mode may name a mechanism ("none"/"token"), not carry a credential.
    ok = McpServerDescriptor(server_id="s", name="s", digest="d", auth_mode="token")
    assert ok.auth_mode == "token"
    with pytest.raises(ValidationError):
        McpServerDescriptor(server_id="s", name="s", digest="d", auth_mode="x" * 40)
    with pytest.raises(ValidationError):
        McpApprovalDecision(
            decision_id="d", server_id="s", ref_name="t", status="registered"
        )
    from agent_driver.contracts.mcp_governance import McpRegistrySnapshot

    dup = McpServerDescriptor(server_id="s", name="s", digest="d")
    with pytest.raises(ValidationError):
        McpRegistrySnapshot(snapshot_id="x", digest="d", server_refs=[dup, dup])


def test_approval_covers_allow_ask_block_out_of_roots_oversized_filtered_no_claim() -> (
    None
):
    allow_policy = McpApprovalPolicy(
        policy_id="p.allow",
        default_action="allow",
        allowed_side_effect_classes=["read_only"],
        roots_boundary=["resource://docs/"],
        max_resource_bytes=100,
    )
    assert (
        evaluate_mcp_approval(_ref("a"), [allow_policy], decision_id="d").status
        == "allowed"
    )

    ask_policy = McpApprovalPolicy(
        policy_id="p.ask",
        default_action="ask",
        allowed_side_effect_classes=["read_only"],
        roots_boundary=["resource://docs/"],
    )
    assert (
        evaluate_mcp_approval(_ref("b"), [ask_policy], decision_id="d").status
        == "asked"
    )

    # out_of_roots wins over the default action.
    oor = evaluate_mcp_approval(
        _ref("c", uri="resource://secret/x"), [allow_policy], decision_id="d"
    )
    assert oor.status == "out_of_roots" and oor.action == "block"

    over = evaluate_mcp_approval(_ref("d", size=1000), [allow_policy], decision_id="d")
    assert over.status == "oversized"

    filtered = evaluate_mcp_approval(
        _ref("e", side_effect="external_action"), [allow_policy], decision_id="d"
    )
    assert filtered.status == "filtered"

    block_policy = McpApprovalPolicy(
        policy_id="p.block",
        default_action="allow",
        blocked_side_effect_classes=["filesystem_write"],
        roots_boundary=["resource://docs/"],
    )
    blocked = evaluate_mcp_approval(
        _ref("f", side_effect="filesystem_write"), [block_policy], decision_id="d"
    )
    assert blocked.status == "blocked"

    # sampling capability with sampling disallowed -> blocked.
    sampling = evaluate_mcp_approval(
        _ref("g", capability="sampling", uri=None, kind="tool"),
        [allow_policy],
        decision_id="d",
    )
    assert sampling.status == "blocked"

    # no matching policy -> no_claim, never a silent allow.
    nc = evaluate_mcp_approval(_ref("h"), [], decision_id="d")
    assert nc.status == "no_claim" and nc.action == "block"


def test_provenance_only_for_allowed_and_carries_no_raw_body() -> None:
    refs = [
        _ref("ok", uri="resource://docs/ok"),
        _ref("blocked", side_effect="external_action"),
    ]
    policy = McpApprovalPolicy(
        policy_id="p",
        default_action="allow",
        allowed_side_effect_classes=["read_only"],
        roots_boundary=["resource://docs/"],
    )
    decisions = build_mcp_approval_decisions(refs, [policy])
    provenance = build_mcp_call_provenance(decisions, refs)
    assert len(provenance) == 1
    row = provenance[0]
    assert row.ref_name == "ok"
    assert row.read_status == "read"
    # provenance dump must not contain a raw-body-shaped key
    dumped = row.model_dump(mode="json")
    assert not any(k.endswith("body") or k == "content" for k in dumped)


def test_seed_reports_for_excel_and_chat_demo_are_redacted_and_multi_status() -> None:
    excel = seed_excel_mcp_governance_report()
    assert excel.product_family == "excel_ai"
    statuses = {d.status for d in excel.approvals_recorded}
    assert {"allowed", "asked", "filtered"}.issubset(statuses)
    # support-bundle projection must be JSON/redaction-safe by construction
    assert excel.support_bundle_projection
    assert excel.usage_summary.registered == len([d for d in excel.approvals_recorded])

    chat = seed_chat_demo_mcp_governance_report()
    assert chat.product_family == "chat_demo"
    assert chat.usage_summary.allowed >= 1
    # No raw-body/credential keys anywhere in the report dumps.
    for report in (excel, chat):
        text = report.model_dump_json()
        assert "resource_body" not in text and "api_key" not in text


def test_governance_artifacts_replay_and_validate_in_008_audit(tmp_path) -> None:
    report = seed_excel_mcp_governance_report()
    snapshot = build_mcp_registry_snapshot(
        snapshot_id="excel_ai-mcp-registry",
        server_roots={
            "demo-docs": ["resource://docs/"],
            "demo-ops": ["resource://jobs/"],
        },
        allowed_roots=["resource://docs/", "resource://jobs/"],
    )
    policies = default_mcp_policies("excel_ai")
    decisions = build_mcp_approval_decisions(snapshot.tool_resource_refs, policies)
    provenance = build_mcp_call_provenance(decisions, snapshot.tool_resource_refs)
    evidence_index = build_mcp_governance_evidence_index(
        report, scenario_ids=["mcp_governance.excel_connectors.v1"]
    )
    write_mcp_governance_artifacts(
        tmp_path / "artifacts",
        snapshot=snapshot,
        decisions=decisions,
        provenance=provenance,
        report=report,
        evidence_index=evidence_index,
    )
    replayed = replay_mcp_governance_from_artifacts(tmp_path / "artifacts")
    audit = audit_validation_evidence(
        [tmp_path / "artifacts"], strict=True, no_live=True
    )
    assert replayed.report_id == report.report_id
    assert (tmp_path / "artifacts" / "mcp_governance_report.md").is_file()
    assert "MCP Governance Compatibility" in render_mcp_governance_markdown(report)
    assert audit["strict_passed"] is True
    assert (
        "mcp_governance.excel_connectors.v1" in audit["validation_run"]["scenario_ids"]
    )
    assert any(
        "mcp_governance_report.json" in path
        for path in audit["dashboard_summary"]["artifact_paths"]
    )


def test_governance_projects_to_hooks_and_adapter_events() -> None:
    report = seed_excel_mcp_governance_report()
    hooks = project_mcp_lifecycle_hook_audit_records(report)
    events = project_mcp_harness_adapter_events(report)
    assert len(hooks) == len(report.approvals_recorded)
    assert {h["verdict"] for h in hooks}.issubset({"allow", "ask", "block"})
    assert all(e["type"] == "mcp_approval" for e in events)
    # allowed decision -> allow verdict; filtered/blocked -> block
    verdict_by_ref = {h["ref_name"]: h["verdict"] for h in hooks}
    assert verdict_by_ref["Quickstart"] == "allow"
    assert verdict_by_ref["search_docs"] == "block"


def test_mcp_governance_cli_writes_and_audits(tmp_path) -> None:
    out = tmp_path / "cli-artifacts"
    args = argparse.Namespace(
        mcp_governance_command="audit",
        scenario="mcp_governance.excel_connectors.v1",
        catalog=None,
        product_family=None,
        host_profile=None,
        servers=None,
        allowed_roots=None,
        max_results=200,
        no_live=True,
        output_dir=str(out),
    )
    assert mcp_governance_audit_command(args) == 0
    for name in (
        "mcp_registry_snapshot.json",
        "mcp_approval_decisions.json",
        "mcp_call_provenance.json",
        "mcp_governance_report.json",
        "mcp_governance_report.md",
        "evidence_index.json",
        "manifest.json",
    ):
        assert (out / name).is_file()
    audit = audit_validation_evidence([out], strict=True, no_live=True)
    assert audit["strict_passed"] is True
