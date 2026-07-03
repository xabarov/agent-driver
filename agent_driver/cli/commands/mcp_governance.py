"""CLI command for deterministic MCP governance artifacts (epic 014)."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.mcp_server.governance import (
    build_mcp_approval_decisions,
    build_mcp_call_provenance,
    build_mcp_governance_compatibility_report,
    build_mcp_governance_evidence_index,
    build_mcp_registry_snapshot,
    default_mcp_policies,
    write_mcp_governance_artifacts,
)


def mcp_governance_command(args: argparse.Namespace) -> int:
    """Dispatch mcp-governance subcommands."""
    if args.mcp_governance_command == "audit":
        return mcp_governance_audit_command(args)
    print(f"mcp-governance error: unsupported subcommand {args.mcp_governance_command}")
    return 2


def mcp_governance_audit_command(args: argparse.Namespace) -> int:
    """Build deterministic MCP governance artifacts without live gates."""
    product_family = args.product_family or _product_family_from_scenario(args.scenario)
    host_profile = args.host_profile or product_family
    allowed_roots = _split_csv(args.allowed_roots) or ["resource://"]
    server_roots = {server: allowed_roots for server in _split_csv(args.servers)}
    snapshot = build_mcp_registry_snapshot(
        snapshot_id=f"{host_profile}-mcp-registry",
        catalog_path=args.catalog,
        server_roots=server_roots or None,
        allowed_roots=allowed_roots,
        max_results=args.max_results,
    )
    policies = default_mcp_policies(product_family)
    decisions = build_mcp_approval_decisions(snapshot.tool_resource_refs, policies)
    provenance = build_mcp_call_provenance(decisions, snapshot.tool_resource_refs)
    report = build_mcp_governance_compatibility_report(
        report_id=f"mcp-governance:{host_profile}",
        product_family=product_family,
        host_profile=host_profile,
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
        metadata={"scenario": args.scenario, "no_runtime_behavior_change": True},
    )
    evidence_index = build_mcp_governance_evidence_index(
        report,
        scenario_ids=[args.scenario],
        include_live_no_claim_gates=bool(args.no_live),
    )
    output_dir = Path(args.output_dir)
    paths = write_mcp_governance_artifacts(
        output_dir,
        snapshot=snapshot,
        decisions=decisions,
        provenance=provenance,
        report=report,
        evidence_index=evidence_index,
    )
    payload = {
        "mode": "deterministic",
        "scenario": args.scenario,
        "catalog": str(args.catalog) if args.catalog else "builtin_mcp_fixture",
        "registry_snapshot": snapshot.model_dump(mode="json"),
        "approval_decisions": [d.model_dump(mode="json") for d in decisions],
        "call_provenance": [p.model_dump(mode="json") for p in provenance],
        "report": report.model_dump(mode="json"),
        "evidence_index": evidence_index.model_dump(mode="json"),
        "written_artifacts": paths,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_resource_body": False,
            "contains_credentials": False,
        },
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def _product_family_from_scenario(scenario: str) -> str:
    if "excel" in scenario:
        return "excel_ai"
    if "chat_demo" in scenario or "research" in scenario:
        return "chat_demo"
    return "mcp_governance"


def _split_csv(value: str | None) -> list[str]:
    if not value:
        return []
    return [item.strip() for item in value.split(",") if item.strip()]


__all__ = ["mcp_governance_audit_command", "mcp_governance_command"]
