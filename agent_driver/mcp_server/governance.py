"""Deterministic MCP governance + provenance evidence (epic 014).

Builds registry snapshots, allowed-roots boundary checks, deterministic approval
decisions, call-provenance rows, redaction-safe support-bundle projections and a
capability-pack-compatible artifact set on top of the existing MCP transport
(``agent_driver/mcp_server``) and client-tool catalog
(``agent_driver/tools/builtin/mcp.py``). No live MCP server is required.
"""

from agent_driver.mcp_server.governance_core import (  # noqa: F401
    _builtin_catalog,
    _chat_demo_policies,
    _excel_policies,
    _match_policy,
    _rationale,
    _server_capabilities,
    _slug,
    _stable_digest,
    _uri_in_roots,
    build_mcp_approval_decisions,
    build_mcp_call_provenance,
    build_mcp_governance_usage_summary,
    build_mcp_registry_snapshot,
    build_server_descriptor,
    default_mcp_policies,
    evaluate_mcp_approval,
    load_mcp_catalog,
    provenance_row_from_ref,
    stable_server_id,
    tool_resource_refs_from_catalog,
)
from agent_driver.mcp_server.governance_report import (  # noqa: F401
    _artifact_manifest_row,
    _pack_id_for_product,
    _primary_mcp_scenario,
    _validation_gate_summary,
    _verdict_for_status,
    build_mcp_governance_compatibility_report,
    build_mcp_governance_evidence_index,
    build_mcp_support_bundle_projection,
    project_mcp_harness_adapter_events,
    project_mcp_lifecycle_hook_audit_records,
    render_mcp_governance_markdown,
    replay_mcp_governance_from_artifacts,
    seed_chat_demo_mcp_governance_report,
    seed_excel_mcp_governance_report,
    write_mcp_governance_artifacts,
)

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
