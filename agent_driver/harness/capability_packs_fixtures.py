"""Capability-pack seed fixtures (pure data builders).

Extracted verbatim from ``harness/capability_packs`` (god-module split, behaviour-neutral).
Leaf layer: builds Harness* fixtures from literals, depending only on the contract types —
imported by the resolution layer in ``capability_packs``, never the reverse (keeps a DAG).
"""

from __future__ import annotations


from agent_driver.contracts.capability_packs import (
    HarnessAdapterManifest,
    HarnessCapabilityPack,
    HarnessReleaseGate,
    HarnessScenarioSpec,
)

# Shared with the resolution layer (imported back there): the inert-slice default skip
# reason for live/optional release gates.
_LIVE_SKIP_REASON = "initial_inert_slice_requires_explicit_live_gate_opt_in"


def seed_excel_workbook_chat_pack() -> HarnessCapabilityPack:
    """Return the seed Excel workbook-chat capability pack."""
    return HarnessCapabilityPack(
        pack_id="excel_workbook_chat",
        version="0.1.0",
        target_product_family="excel_ai",
        status="experimental",
        provider_route_requirements={
            "route_family": "openrouter",
            "preflight_required_before_live": True,
            "model_profile_required": True,
        },
        policy_profile_defaults={
            "mode": "observe",
            "enabled_policy_ids": [
                "workbook_context_required",
                "side_effect_transaction_required",
                "provider_request_shape_preflight",
            ],
            "required_evidence": [
                "context_provenance",
                "artifact_provenance",
                "side_effect_transactions",
            ],
        },
        supervision_expectations={
            "heartbeat_required": True,
            "reconnect_cursor_required": True,
            "process_death_truthfulness_required": True,
        },
        required_evidence=[
            "context_provenance",
            "artifact_provenance",
            "side_effect_transactions",
            "validation_gates",
        ],
        required_skills=["workbook_context", "chart_artifact"],
        context_requirements=["workbook_scope", "worksheet_selection"],
        side_effect_classes=["write", "transactional_edit"],
        release_gates=_seed_release_gates(),
        rollout_mode="inert",
        compatibility={
            "schema_version": "2026-07-03",
            "min_agent_driver": "0.1.0",
        },
        owners=["agent-driver-harness", "excel-ai-adapter"],
        ownership_notes=[
            "agent-driver-harness owns generic pack contracts and resolver behavior.",
            "excel-ai-adapter owns workbook scenario commands, ports and product evidence paths.",
            "Update this pack when Excel provider routes, workbook UI warnings, edit transaction semantics or benchmark gates change.",
        ],
        review_checklist=[
            "Reject changes that store workbook ids, auth state or provider key values.",
            "Reject scenarios that require live OpenRouter, Phoenix, Playwright or benchmark gates without an explicit blast-radius reason.",
            "Keep workbook-specific expectations in the Excel pack or adapter manifest, not generic resolver code.",
        ],
        notes=[
            "Pack metadata must not contain secrets; env var names stay adapter-owned.",
            "Initial selection only projects validation metadata and never mutates runtime behavior.",
        ],
    )


def seed_deep_research_chat_demo_pack() -> HarnessCapabilityPack:
    """Return the seed chat-demo deep-research capability pack."""
    return HarnessCapabilityPack(
        pack_id="deep_research_chat_demo",
        version="0.1.0",
        target_product_family="chat_demo",
        status="experimental",
        provider_route_requirements={
            "route_family": "openrouter",
            "preflight_required_before_live": True,
            "streaming_required": True,
        },
        policy_profile_defaults={
            "mode": "observe",
            "enabled_policy_ids": [
                "required_source_evidence",
                "artifact_provenance_required",
                "tool_loop_no_progress",
                "provider_request_shape_preflight",
            ],
            "required_evidence": [
                "source_evidence",
                "artifact_provenance",
            ],
        },
        supervision_expectations={
            "heartbeat_required": True,
            "budget_stop_visible": True,
            "reconnect_cursor_required": True,
        },
        required_evidence=[
            "source_evidence",
            "artifact_provenance",
            "validation_gates",
        ],
        required_skills=["source-triangulation"],
        context_requirements=["workspace_artifacts", "research_sources"],
        side_effect_classes=["artifact_write"],
        release_gates=_seed_release_gates(),
        rollout_mode="inert",
        compatibility={
            "schema_version": "2026-07-03",
            "min_agent_driver": "0.1.0",
        },
        owners=["agent-driver-harness", "chat-demo-adapter"],
        ownership_notes=[
            "agent-driver-harness owns generic pack contracts and resolver behavior.",
            "chat-demo-adapter owns deep-research probes, trace-summary endpoints and workspace artifact evidence paths.",
            "Update this pack when research tool policy, source ledgers, report artifacts, steering or reconnect expectations change.",
        ],
        review_checklist=[
            "Reject changes that store user session state or provider key values.",
            "Reject scenarios that treat skipped live/provider/UI gates as passed evidence.",
            "Keep research artifact/source expectations in the chat-demo pack or adapter manifest, not generic resolver code.",
        ],
        notes=[
            "Live provider, Phoenix, Playwright and benchmark gates start skipped.",
            "Scenario evidence should reference source ledgers and report artifacts, not raw fetched pages.",
        ],
    )


def seed_capability_packs() -> dict[str, HarnessCapabilityPack]:
    """Return all built-in deterministic seed packs by id."""
    packs = (
        seed_excel_workbook_chat_pack(),
        seed_deep_research_chat_demo_pack(),
    )
    return {pack.pack_id: pack for pack in packs}


def seed_scenario_specs() -> dict[str, HarnessScenarioSpec]:
    """Return minimal two-product scenario fixtures."""
    scenarios = [
        HarnessScenarioSpec(
            scenario_id="excel.workbook_context.transaction.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Use workbook context, create a chart artifact, and apply a safe edit transaction.",
            required_tools=["workbook_context", "chart_artifact", "excel_apply_edit"],
            required_evidence=[
                "context_provenance",
                "artifact_provenance",
                "side_effect_transactions",
            ],
            expected_policy_verdicts=[
                "workbook_context_required",
                "side_effect_transaction_required",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "benchmark_delta",
            ],
            artifact_paths=[
                "docs/benchmarks/",
                "backend/benchmark_reports/",
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="chat_demo.deep_research.source_report.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Research a source-grounded answer and write a durable report artifact.",
            required_tools=["web_search", "web_fetch", "artifact_write"],
            required_evidence=["source_evidence", "artifact_provenance"],
            expected_policy_verdicts=[
                "required_source_evidence",
                "artifact_provenance_required",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "research/report.md",
                "research/sources.jsonl",
                "/tmp/chat-demo-live-capability-pack-research-report",
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="harness_adapter.acp.basic_stream.v1",
            status="deterministic",
            product_adapter_id="acp",
            prompt_seed="Project a basic ACP run stream into harness adapter rows.",
            required_evidence=[
                "adapter_event_projection",
                "cursor_reconnect",
                "redaction_validation",
            ],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["phoenix_trace", "playwright_ui"],
            artifact_paths=[
                "adapter_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=[
                "live gates remain no_claim in offline compatibility mode"
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="harness_adapter.a2a.basic_task.v1",
            status="deterministic",
            product_adapter_id="a2a",
            prompt_seed="Project a basic A2A task stream into harness adapter rows.",
            required_evidence=[
                "adapter_event_projection",
                "cursor_reconnect",
                "artifact_projection",
            ],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "adapter_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=[
                "live gates remain no_claim in offline compatibility mode"
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="harness_adapter.openai_server.basic_run.v1",
            status="deterministic",
            product_adapter_id="openai_server",
            prompt_seed="Project an OpenAI-compatible server run into harness adapter rows.",
            required_evidence=[
                "adapter_event_projection",
                "cursor_reconnect",
                "approval_projection",
            ],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["openrouter_live_preflight", "phoenix_trace"],
            artifact_paths=[
                "adapter_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=[
                "live gates remain no_claim in offline compatibility mode"
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="harness_adapter.chat_demo.deep_research.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Project deep-research source/report artifacts into adapter refs.",
            required_evidence=[
                "adapter_event_projection",
                "source_evidence",
                "artifact_provenance",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "adapter_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=["UI evidence is no_claim unless Playwright is executed"],
        ),
        HarnessScenarioSpec(
            scenario_id="harness_adapter.excel_workbook_chat.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Project workbook evidence, approvals and report artifacts into adapter refs.",
            required_evidence=[
                "adapter_event_projection",
                "workbook_context",
                "artifact_provenance",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
                "benchmark_delta",
            ],
            artifact_paths=[
                "adapter_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=[
                "Excel UI evidence is no_claim unless Playwright is executed"
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="provider_catalog.plugin_registry.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Validate built-in provider plugin registry ids, aliases and descriptor bridge rows.",
            required_evidence=[
                "provider_plugin_registry",
                "provider_compatibility_report",
                "redaction_validation",
            ],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "provider_compatibility_report.json",
                "provider_catalog.json",
            ],
            skip_conditions=["provider catalog live gates remain no_claim offline"],
        ),
        HarnessScenarioSpec(
            scenario_id="provider_catalog.sanitizer_matrix.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Validate provider request sanitizer fixtures and downgrade/no-claim verdicts.",
            required_evidence=[
                "provider_sanitizer_matrix",
                "provider_compatibility_report",
                "redaction_validation",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace", "benchmark_delta"],
            artifact_paths=[
                "provider_sanitizer_matrix.json",
                "provider_compatibility_report.json",
            ],
            skip_conditions=["quality/cost claims require benchmark artifacts"],
        ),
        HarnessScenarioSpec(
            scenario_id="provider_catalog.openrouter_preflight.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Generalize OpenRouter preflight into deterministic provider preflight report shape.",
            required_evidence=[
                "provider_preflight_report",
                "provider_compatibility_report",
            ],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["openrouter_live_preflight", "phoenix_trace"],
            artifact_paths=[
                "provider_compatibility_report.json",
                "validation_gates.json",
            ],
            skip_conditions=["live OpenRouter claims require explicit opt-in and Phoenix evidence"],
        ),
        HarnessScenarioSpec(
            scenario_id="provider_catalog.excel_workbook_routes.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Model Excel workbook chat provider routes for tools, structured output, vision and long context.",
            required_evidence=[
                "provider_routing_plan",
                "provider_compatibility_report",
                "provider_sanitizer_matrix",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
                "benchmark_delta",
            ],
            artifact_paths=[
                "provider_compatibility_report.json",
                "provider_sanitizer_matrix.json",
            ],
            skip_conditions=["Excel UI evidence is no_claim unless provider work changes UI"],
        ),
        HarnessScenarioSpec(
            scenario_id="provider_catalog.chat_demo_research_routes.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Model chat-demo deep research routes for source tools, reports, long context and live probes.",
            required_evidence=[
                "provider_routing_plan",
                "provider_compatibility_report",
                "provider_sanitizer_matrix",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
                "benchmark_delta",
            ],
            artifact_paths=[
                "provider_compatibility_report.json",
                "provider_sanitizer_matrix.json",
            ],
            skip_conditions=["chat-demo UI evidence is no_claim unless provider work changes UI"],
        ),
        HarnessScenarioSpec(
            scenario_id="skills_lifecycle.inventory_lock_diff.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Build skill inventory snapshots, lockfiles and reload diffs without reading raw skill bodies.",
            required_evidence=[
                "skill_inventory_snapshot",
                "skill_lockfile",
                "skill_reload_diff",
                "redaction_validation",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "skills_inventory_snapshot.json",
                "skills_lock.json",
                "skills_reload_diff.json",
            ],
            skip_conditions=["live skill reload traces remain no_claim offline"],
        ),
        HarnessScenarioSpec(
            scenario_id="skills_lifecycle.selection_evidence.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Explain selected, filtered, skipped, disabled, blocked and no-claim skills without raw skill body leakage.",
            required_evidence=[
                "skill_selection_evidence",
                "skill_lifecycle_compatibility_report",
                "redaction_validation",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "skills_selection_decisions.json",
                "skills_compatibility_report.json",
            ],
            skip_conditions=["selection changes do not alter runtime prompt behavior"],
        ),
        HarnessScenarioSpec(
            scenario_id="skills_lifecycle.invocation_provenance.v1",
            status="deterministic",
            product_adapter_id="provider_catalog",
            prompt_seed="Project viewed skill bodies and supporting files into invocation/provenance refs.",
            required_evidence=[
                "skill_lifecycle_compatibility_report",
                "skill_selection_evidence",
                "support_bundle_artifact",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "skills_invocation_records.json",
                "skills_compatibility_report.json",
            ],
            skip_conditions=["raw skill contents are excluded from support bundles"],
        ),
        HarnessScenarioSpec(
            scenario_id="skills_lifecycle.excel_workbook_skills.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Model workbook, chart, transaction and business workflow skill locks for Excel AI.",
            required_evidence=[
                "skill_inventory_snapshot",
                "skill_lockfile",
                "skill_selection_evidence",
                "skill_lifecycle_compatibility_report",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
                "benchmark_delta",
            ],
            artifact_paths=[
                "skills_inventory_snapshot.json",
                "skills_lock.json",
                "skills_compatibility_report.json",
            ],
            skip_conditions=[
                "Excel UI skill rows are no_claim unless Playwright is executed",
                "quality/cost/latency claims require benchmark artifacts",
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="skills_lifecycle.chat_demo_research_skills.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Model deep research report, citation, triangulation, literature and provider-doc skills.",
            required_evidence=[
                "skill_inventory_snapshot",
                "skill_lockfile",
                "skill_selection_evidence",
                "skill_lifecycle_compatibility_report",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
                "benchmark_delta",
            ],
            artifact_paths=[
                "skills_inventory_snapshot.json",
                "skills_lock.json",
                "skills_compatibility_report.json",
            ],
            skip_conditions=[
                "live research/provider/Phoenix evidence is no_claim unless executed",
                "deep research UI skill rows are no_claim unless Playwright is executed",
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="lifecycle_hooks.tool_transform_audit.v1",
            status="deterministic",
            product_adapter_id="acp",
            prompt_seed="Record a tool hook transform audit row and re-run guardrail checks.",
            required_evidence=[
                "lifecycle_hook_audit",
                "adapter_event_projection",
                "redaction_validation",
            ],
            expected_policy_verdicts=["guardrails_after_transform"],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "lifecycle_hook_compatibility_report.json",
                "lifecycle_hook_audit.jsonl",
            ],
            skip_conditions=["live hook trace is no_claim unless explicitly run"],
        ),
        HarnessScenarioSpec(
            scenario_id="lifecycle_hooks.approval_interrupt_audit.v1",
            status="deterministic",
            product_adapter_id="acp",
            prompt_seed="Record approval requested/resolved and interrupt hook rows.",
            required_evidence=[
                "lifecycle_hook_audit",
                "approval_projection",
                "interrupt_projection",
            ],
            expected_policy_verdicts=["approval_requested"],
            deterministic_gate_ids=["deterministic_tests"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "lifecycle_hook_compatibility_report.json",
                "lifecycle_hook_audit.jsonl",
            ],
            skip_conditions=["host UI approval path is no_claim in offline mode"],
        ),
        HarnessScenarioSpec(
            scenario_id="lifecycle_hooks.excel_workbook_policy.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Model workbook context, edit approval and chart artifact hooks.",
            required_evidence=[
                "lifecycle_hook_audit",
                "workbook_context",
                "artifact_provenance",
            ],
            expected_policy_verdicts=[
                "workbook_context_required",
                "side_effect_transaction_required",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "lifecycle_hook_compatibility_report.json",
                "lifecycle_hook_audit.jsonl",
            ],
            skip_conditions=["Excel hook UI rows are no_claim unless UI changes land"],
        ),
        HarnessScenarioSpec(
            scenario_id="lifecycle_hooks.chat_demo_research_policy.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Model source evidence, report artifact and workspace write hooks.",
            required_evidence=[
                "lifecycle_hook_audit",
                "source_evidence",
                "artifact_provenance",
            ],
            expected_policy_verdicts=[
                "required_source_evidence",
                "artifact_provenance_required",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "lifecycle_hook_compatibility_report.json",
                "lifecycle_hook_audit.jsonl",
            ],
            skip_conditions=[
                "deep research hook UI rows are no_claim unless UI changes land"
            ],
        ),
        HarnessScenarioSpec(
            scenario_id="durable_lifecycle.session_run_records.v1",
            status="deterministic",
            product_adapter_id="acp",
            prompt_seed="Write and read durable session/run records and stable reconnect cursors.",
            required_evidence=[
                "durable_lifecycle_records",
                "redaction_validation",
                "cursor_reconnect",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "durable_lifecycle_compatibility_report.json",
                "durable_lifecycle_records.json",
            ],
            skip_conditions=["restart/live gates remain no_claim in offline mode"],
        ),
        HarnessScenarioSpec(
            scenario_id="durable_lifecycle.interrupt_resume_plan.v1",
            status="deterministic",
            product_adapter_id="acp",
            prompt_seed="Generate resume plans from checkpoint, interrupt and approval records.",
            required_evidence=[
                "checkpoint_index",
                "interrupt_records",
                "approval_records",
                "resume_plan",
            ],
            expected_policy_verdicts=[
                "checkpoint_only_no_claim",
                "approval_required",
                "side_effect_unsafe",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "durable_lifecycle_compatibility_report.json",
                "durable_lifecycle_records.json",
            ],
            skip_conditions=["live resume claims require Phoenix restart evidence"],
        ),
        HarnessScenarioSpec(
            scenario_id="durable_lifecycle.background_attach_replay.v1",
            status="deterministic",
            product_adapter_id="openai_server",
            prompt_seed="Distinguish active lease attach, replay-only, orphaned and terminal runs.",
            required_evidence=[
                "background_lease",
                "background_logs",
                "attach_plan",
                "adapter_event_projection",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=["phoenix_trace"],
            artifact_paths=[
                "durable_lifecycle_compatibility_report.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=["process restart smoke is defined but not required"],
        ),
        HarnessScenarioSpec(
            scenario_id="durable_lifecycle.excel_workbook_pause.v1",
            status="deterministic",
            product_adapter_id="excel_ai",
            prompt_seed="Model a paused workbook run with approval and workbook context artifacts.",
            required_evidence=[
                "durable_lifecycle_records",
                "workbook_context",
                "approval_records",
                "resume_plan",
            ],
            expected_policy_verdicts=[
                "workbook_context_required",
                "side_effect_idempotency_no_claim",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "durable_lifecycle_compatibility_report.json",
                "durable_lifecycle_records.json",
            ],
            skip_conditions=["Excel UI evidence is no_claim unless UI changes land"],
        ),
        HarnessScenarioSpec(
            scenario_id="durable_lifecycle.chat_demo_research_pause.v1",
            status="deterministic",
            product_adapter_id="chat_demo",
            prompt_seed="Model long-running research with sources, report artifact and steering interrupt.",
            required_evidence=[
                "durable_lifecycle_records",
                "source_evidence",
                "artifact_provenance",
                "background_logs",
                "resume_plan",
            ],
            expected_policy_verdicts=[
                "workspace_side_effect_no_claim",
                "replay_available",
            ],
            deterministic_gate_ids=["deterministic_tests", "support_bundle_artifact"],
            optional_live_gate_ids=[
                "openrouter_live_preflight",
                "phoenix_trace",
                "playwright_ui",
            ],
            artifact_paths=[
                "durable_lifecycle_compatibility_report.json",
                "durable_lifecycle_records.json",
                "adapter_events.jsonl",
            ],
            skip_conditions=[
                "chat-demo UI evidence is no_claim unless UI changes land"
            ],
        ),
    ]
    return {scenario.scenario_id: scenario for scenario in scenarios}


def seed_adapter_manifests() -> dict[str, HarnessAdapterManifest]:
    """Return adapter manifests with commands and env names, never values."""
    adapters = [
        HarnessAdapterManifest(
            adapter_id="excel_ai",
            product_name="Excel AI",
            product_family="excel_ai",
            expected_ports={
                "backend": 8002,
                "frontend": 5173,
                "phoenix": 6006,
                "postgres": 5433,
                "redis": 6380,
                "minio": 9000,
            },
            env_var_names=[
                "LLM_API_KEY",
                "OPENROUTER_API_KEY",
                "AGENT_DRIVER_API_KEY",
                "AGENT_DRIVER_REPO",
                "EXCEL_AI_BACKEND_DIR",
                "EXCEL_AI_BACKEND_PYTHON",
                "EXCEL_PHOENIX_ENABLED",
                "EXCEL_PHOENIX_ENDPOINT",
                "EXCEL_PHOENIX_PROJECT",
            ],
            deterministic_commands=[
                'test -n "${EXCEL_AI_BACKEND_DIR:-}" && test -x "${EXCEL_AI_BACKEND_PYTHON:-${EXCEL_AI_BACKEND_DIR}/venv/bin/python}" && PYTHONPATH="${AGENT_DRIVER_REPO:-.}:${EXCEL_AI_BACKEND_DIR}" "${EXCEL_AI_BACKEND_PYTHON:-${EXCEL_AI_BACKEND_DIR}/venv/bin/python}" -m pytest -p no:cacheprovider "${EXCEL_AI_BACKEND_DIR}/tests/test_provenance_adapter.py" "${EXCEL_AI_BACKEND_DIR}/tests/test_chart_flat_args.py" "${EXCEL_AI_BACKEND_DIR}/tests/test_edit_benchmark_metrics.py" -q',
            ],
            optional_live_commands=[
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-openrouter-preflight',
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-excel-trace-smoke',
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-excel-trace-ui-review',
                'test -n "${EXCEL_AI_BACKEND_DIR:-}" && cd "${EXCEL_AI_BACKEND_DIR}" && EXCEL_PHOENIX_ENABLED=true EXCEL_PHOENIX_PROJECT=excel-ai EXCEL_PHOENIX_ENDPOINT=http://localhost:6006 EXCEL_BENCHMARK_RUN_ID=capability-pack-excel-workbook-chat "${EXCEL_AI_BACKEND_PYTHON:-${EXCEL_AI_BACKEND_DIR}/venv/bin/python}" -m tests.benchmark.runner',
            ],
            trace_endpoints=["Phoenix project excel-ai"],
            support_bundle_endpoints=["product adapter support bundle"],
            benchmark_commands=["backend/venv/bin/python -m tests.benchmark.runner"],
            playwright_specs=[
                "frontend/e2e/chart-generation.spec.ts",
                "frontend/e2e/qa-v1-edit-compute-apply.spec.ts",
            ],
            artifact_output_paths=["docs/benchmarks/", "backend/benchmark_reports/"],
            known_non_goals=[
                "Do not store workbook ids, session auth or provider keys in packs.",
            ],
        ),
        HarnessAdapterManifest(
            adapter_id="chat_demo",
            product_name="agent-driver chat-demo",
            product_family="chat_demo",
            expected_ports={"backend": 8010, "frontend": 5174, "phoenix": 6006},
            env_var_names=[
                "AGENT_DRIVER_PROVIDER",
                "AGENT_DRIVER_API_KEY",
                "OPENROUTER_API_KEY",
                "LLM_API_KEY",
                "AGENT_DRIVER_REPO",
                "AGENT_DRIVER_BASE_URL",
                "AGENT_DRIVER_MODEL",
                "PHOENIX_COLLECTOR_ENDPOINT",
                "PHOENIX_PROJECT_NAME",
            ],
            deterministic_commands=[
                'cd "${AGENT_DRIVER_REPO:-.}/examples/chat-demo/backend" && PYTHONPATH=../../.. uv run python -m pytest -q tests/test_run_trace_summary.py tests/test_workspace.py',
                'cd "${AGENT_DRIVER_REPO:-.}" && uv run python -m pytest -p no:cacheprovider tests/observability/test_run_trace_summary.py tests/observability/test_support_bundle.py -q',
            ],
            optional_live_commands=[
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-openrouter-preflight',
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-chat-demo-trace-smoke',
                'cd "${AGENT_DRIVER_REPO:-.}" && make policy-supervision-chat-demo-trace-ui-review',
                'cd "${AGENT_DRIVER_REPO:-.}" && CHAT_DEMO_URL=http://localhost:5174 CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-capability-pack-research-report uv run python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario research-report',
            ],
            trace_endpoints=["/api/chat/runs/{run_id}/trace-summary"],
            support_bundle_endpoints=["/api/chat/runs/{run_id}/trace-summary"],
            benchmark_commands=[
                'cd "${AGENT_DRIVER_REPO:-.}" && CHAT_DEMO_URL=http://localhost:5174 uv run python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario research-report',
            ],
            playwright_specs=["examples/chat-demo/frontend/tests/e2e/*.py"],
            artifact_output_paths=[
                "/tmp/chat-demo-live-capability-pack-research-report"
            ],
            known_non_goals=[
                "Do not store user session state or provider key values in packs.",
            ],
        ),
    ]
    return {adapter.adapter_id: adapter for adapter in adapters}


def _seed_release_gates() -> list[HarnessReleaseGate]:
    return [
        HarnessReleaseGate(
            gate_id="deterministic_tests",
            gate_class="deterministic",
            required=True,
        ),
        HarnessReleaseGate(
            gate_id="support_bundle_artifact",
            gate_class="replay",
            required=True,
        ),
        HarnessReleaseGate(
            gate_id="openrouter_live_preflight",
            gate_class="live_provider",
            required=False,
            skip_condition=_LIVE_SKIP_REASON,
        ),
        HarnessReleaseGate(
            gate_id="phoenix_trace",
            gate_class="phoenix",
            required=False,
            skip_condition=_LIVE_SKIP_REASON,
        ),
        HarnessReleaseGate(
            gate_id="playwright_ui",
            gate_class="playwright",
            required=False,
            skip_condition="only_required_for_user_visible_changes",
        ),
        HarnessReleaseGate(
            gate_id="benchmark_delta",
            gate_class="benchmark",
            required=False,
            skip_condition="only_required_for_quality_cost_latency_changes",
        ),
    ]


__all__ = [
    "seed_excel_workbook_chat_pack",
    "seed_deep_research_chat_demo_pack",
    "seed_capability_packs",
    "seed_scenario_specs",
    "seed_adapter_manifests",
]
