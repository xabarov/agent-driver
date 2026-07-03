"""Pure capability-pack fixtures and resolution helpers."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.capability_packs import (
    CapabilityPackResolution,
    EvidenceArtifactIndex,
    EvidenceArtifactRef,
    HarnessAdapterManifest,
    HarnessCapabilityPack,
    HarnessReleaseGate,
    HarnessScenarioSpec,
)
from agent_driver.contracts.policy import ValidationGateResult

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
                "/tmp/chat-demo-live-<tag>",
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
                "EXCEL_PHOENIX_ENABLED",
                "EXCEL_PHOENIX_ENDPOINT",
                "EXCEL_PHOENIX_PROJECT",
            ],
            deterministic_commands=[
                "backend/venv/bin/python -m pytest -p no:cacheprovider backend/tests/<test>.py -q",
                "PYTHONPATH=/mnt/share/gitlab_projects/agent-driver-gitlab/agent-driver:/mnt/share/gitlab_projects/excel_ai/backend venv/bin/python -m pytest -p no:cacheprovider backend/tests/<test>.py -q",
            ],
            optional_live_commands=[
                "make phoenix-smoke",
                "EXCEL_PHOENIX_ENABLED=true EXCEL_PHOENIX_PROJECT=excel-ai EXCEL_PHOENIX_ENDPOINT=http://localhost:6006 EXCEL_BENCHMARK_RUN_ID=<tag> backend/venv/bin/python -m tests.benchmark.runner",
            ],
            trace_endpoints=["Phoenix project excel-ai"],
            support_bundle_endpoints=["product adapter support bundle"],
            benchmark_commands=["backend/venv/bin/python -m tests.benchmark.runner"],
            playwright_specs=["frontend/e2e/<spec>.ts"],
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
                "AGENT_DRIVER_BASE_URL",
                "AGENT_DRIVER_MODEL",
                "PHOENIX_COLLECTOR_ENDPOINT",
                "PHOENIX_PROJECT_NAME",
            ],
            deterministic_commands=[
                "PYTHONPATH=../../.. uv run python -m pytest -q",
                ".venv/bin/python -m pytest -p no:cacheprovider tests/observability/test_run_trace_summary.py -q",
            ],
            optional_live_commands=[
                "CHAT_DEMO_URL=http://localhost:5174 CHAT_DEMO_LIVE_ARTIFACT_DIR=/tmp/chat-demo-live-<tag> .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario <scenario>",
                "make policy-supervision-chat-demo-trace-ui-review",
            ],
            trace_endpoints=["/api/chat/runs/{run_id}/trace-summary"],
            support_bundle_endpoints=["/api/chat/runs/{run_id}/trace-summary"],
            benchmark_commands=[
                "CHAT_DEMO_URL=http://localhost:5174 .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --scenario <scenario>",
            ],
            playwright_specs=["examples/chat-demo/frontend/tests/e2e/*.py"],
            artifact_output_paths=["/tmp/chat-demo-live-<tag>"],
            known_non_goals=[
                "Do not store user session state or provider key values in packs.",
            ],
        ),
    ]
    return {adapter.adapter_id: adapter for adapter in adapters}


def resolve_capability_pack(
    pack: HarnessCapabilityPack,
    *,
    adapter_manifest: HarnessAdapterManifest | None = None,
    scenario_specs: list[HarnessScenarioSpec] | None = None,
    gate_results: list[ValidationGateResult] | None = None,
    overrides: dict[str, Any] | None = None,
) -> CapabilityPackResolution:
    """Combine pack defaults, adapter metadata and run overrides."""
    overrides = dict(overrides or {})
    scenarios = list(scenario_specs or [])
    gate_statuses: dict[str, str] = {}
    skipped_gate_reasons: dict[str, str] = {}
    selected_gate_ids: list[str] = []
    evidence_gates: list[ValidationGateResult] = []

    provided_results = {gate.gate_id: gate for gate in (gate_results or [])}
    for gate in pack.release_gates:
        selected_gate_ids.append(gate.gate_id)
        provided = provided_results.get(gate.gate_id)
        status = provided.status if provided is not None else _default_gate_status(gate)
        reason = (
            provided.reason
            if provided is not None and provided.reason
            else gate.skip_condition
        )
        gate_statuses[gate.gate_id] = status
        if status == "skipped":
            skipped_gate_reasons[gate.gate_id] = reason or _LIVE_SKIP_REASON
        evidence_gates.append(
            provided
            if provided is not None
            else ValidationGateResult(
                gate_id=gate.gate_id,
                status=status,
                command=gate.command,
                reason=reason,
                redacted_metadata={
                    "gate_class": gate.gate_class,
                    "required": gate.required,
                },
            )
        )

    for scenario in scenarios:
        for gate_id in scenario.deterministic_gate_ids + scenario.optional_live_gate_ids:
            if gate_id not in selected_gate_ids:
                selected_gate_ids.append(gate_id)
                status = "skipped" if gate_id in scenario.optional_live_gate_ids else "not_run"
                gate_statuses[gate_id] = status
                if status == "skipped":
                    skipped_gate_reasons[gate_id] = _LIVE_SKIP_REASON
                evidence_gates.append(
                    ValidationGateResult(
                        gate_id=gate_id,
                        status=status,
                        reason=skipped_gate_reasons.get(gate_id),
                    )
                )

    for gate in gate_results or []:
        if gate.gate_id not in selected_gate_ids:
            selected_gate_ids.append(gate.gate_id)
            gate_statuses[gate.gate_id] = gate.status
            if gate.status == "skipped" and gate.reason:
                skipped_gate_reasons[gate.gate_id] = gate.reason
            evidence_gates.append(gate)

    required_evidence = _merge_unique(
        pack.required_evidence,
        *(scenario.required_evidence for scenario in scenarios),
        _string_list(overrides.get("required_evidence")),
    )
    required_skills = _merge_unique(
        pack.required_skills,
        _string_list(overrides.get("required_skills")),
    )
    context_requirements = _merge_unique(
        pack.context_requirements,
        _string_list(overrides.get("context_requirements")),
    )

    evidence_index = EvidenceArtifactIndex(
        index_id=str(overrides.get("evidence_index_id") or f"{pack.pack_id}:dry-run"),
        pack_id=pack.pack_id,
        pack_version=pack.version,
        scenario_ids=[scenario.scenario_id for scenario in scenarios],
        gates=evidence_gates,
        artifacts=[
            EvidenceArtifactRef(
                artifact_id=f"{gate_id}:skip",
                artifact_type="skip_justification",
                gate_id=gate_id,
                redacted_metadata={"reason": reason},
            )
            for gate_id, reason in sorted(skipped_gate_reasons.items())
        ],
        skipped_gate_ids=sorted(skipped_gate_reasons),
    )

    return CapabilityPackResolution(
        pack_id=pack.pack_id,
        pack_version=pack.version,
        adapter_id=adapter_manifest.adapter_id if adapter_manifest else None,
        scenario_ids=[scenario.scenario_id for scenario in scenarios],
        selected_gate_ids=selected_gate_ids,
        required_evidence=required_evidence,
        required_skills=required_skills,
        context_requirements=context_requirements,
        provider_route_requirements={
            **pack.provider_route_requirements,
            **_dict_value(overrides.get("provider_route_requirements")),
        },
        policy_profile_defaults={
            **pack.policy_profile_defaults,
            **_dict_value(overrides.get("policy_profile_defaults")),
        },
        supervision_expectations={
            **pack.supervision_expectations,
            **_dict_value(overrides.get("supervision_expectations")),
        },
        gate_statuses=gate_statuses,
        skipped_gate_ids=sorted(skipped_gate_reasons),
        skipped_gate_reasons=skipped_gate_reasons,
        deterministic_commands=(
            adapter_manifest.deterministic_commands if adapter_manifest else []
        ),
        optional_live_commands=(
            adapter_manifest.optional_live_commands if adapter_manifest else []
        ),
        evidence_index=evidence_index,
        rollout_mode=str(overrides.get("rollout_mode") or pack.rollout_mode),
        compatibility=pack.compatibility,
        redacted_metadata={
            "selected": True,
            "status": pack.status,
            "no_runtime_behavior_change": True,
        },
    )


def build_capability_pack_resolution(metadata: dict[str, Any]) -> dict[str, Any]:
    """Return a redaction-safe pack resolution summary from run metadata."""
    existing = metadata.get("capability_pack_resolution")
    if isinstance(existing, dict):
        return CapabilityPackResolution.model_validate(existing).model_dump(mode="json")

    pack = _pack_from_metadata(metadata)
    if pack is None:
        return _empty_resolution().model_dump(mode="json")

    adapter = _adapter_from_metadata(metadata, pack)
    scenario_specs = _scenarios_from_metadata(metadata, adapter)
    resolution = resolve_capability_pack(
        pack,
        adapter_manifest=adapter,
        scenario_specs=scenario_specs,
        gate_results=_gate_results_from_metadata(metadata),
        overrides=_dict_value(metadata.get("capability_pack_overrides")),
    )
    return resolution.model_dump(mode="json")


def build_capability_pack_dry_run(
    *,
    pack_id: str,
    adapter_id: str | None = None,
    scenario_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Resolve a seed capability pack without executing any commands."""
    packs = seed_capability_packs()
    pack = packs.get(pack_id)
    if pack is None:
        raise ValueError(f"unknown capability pack: {pack_id}")

    adapters = seed_adapter_manifests()
    adapter = adapters.get(adapter_id or _default_adapter_id(pack))
    if adapter is None:
        raise ValueError(f"unknown capability adapter: {adapter_id}")

    scenarios_by_id = seed_scenario_specs()
    selected_ids = list(scenario_ids or _default_scenario_ids(adapter.adapter_id))
    scenarios: list[HarnessScenarioSpec] = []
    for scenario_id in selected_ids:
        scenario = scenarios_by_id.get(scenario_id)
        if scenario is None:
            raise ValueError(f"unknown capability scenario: {scenario_id}")
        if scenario.product_adapter_id != adapter.adapter_id:
            raise ValueError(
                f"scenario {scenario_id} belongs to adapter "
                f"{scenario.product_adapter_id}, not {adapter.adapter_id}"
            )
        scenarios.append(scenario)

    resolution = resolve_capability_pack(
        pack,
        adapter_manifest=adapter,
        scenario_specs=scenarios,
    )
    return {
        "mode": "dry_run",
        "executed_commands": [],
        "would_execute": {
            "deterministic_commands": resolution.deterministic_commands,
            "optional_live_commands": resolution.optional_live_commands,
        },
        "capability_pack_resolution": resolution.model_dump(mode="json"),
        "evidence_index": (
            resolution.evidence_index.model_dump(mode="json")
            if resolution.evidence_index is not None
            else None
        ),
        "redaction": {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_command_output": False,
        },
    }


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


def _default_gate_status(gate: HarnessReleaseGate) -> str:
    if gate.required:
        return "not_run"
    return "skipped"


def _pack_from_metadata(metadata: dict[str, Any]) -> HarnessCapabilityPack | None:
    raw_pack = metadata.get("capability_pack") or metadata.get("harness_capability_pack")
    if isinstance(raw_pack, dict):
        return HarnessCapabilityPack.model_validate(raw_pack)
    pack_id = metadata.get("capability_pack_id") or metadata.get(
        "harness_capability_pack_id"
    )
    if not isinstance(pack_id, str) or not pack_id:
        return None
    return seed_capability_packs().get(pack_id)


def _adapter_from_metadata(
    metadata: dict[str, Any], pack: HarnessCapabilityPack
) -> HarnessAdapterManifest | None:
    raw_adapter = metadata.get("capability_adapter_manifest")
    if isinstance(raw_adapter, dict):
        return HarnessAdapterManifest.model_validate(raw_adapter)
    adapter_id = metadata.get("capability_adapter_id")
    if not isinstance(adapter_id, str) or not adapter_id:
        adapter_id = {
            "excel_ai": "excel_ai",
            "chat_demo": "chat_demo",
        }.get(pack.target_product_family, "")
    return seed_adapter_manifests().get(adapter_id)


def _scenarios_from_metadata(
    metadata: dict[str, Any], adapter: HarnessAdapterManifest | None
) -> list[HarnessScenarioSpec]:
    raw_scenarios = metadata.get("capability_scenarios")
    if isinstance(raw_scenarios, list):
        return [
            HarnessScenarioSpec.model_validate(item)
            for item in raw_scenarios
            if isinstance(item, dict)
        ]
    scenario_ids = _string_list(metadata.get("capability_scenario_ids"))
    if not scenario_ids and adapter is not None:
        scenario_ids = [
            scenario.scenario_id
            for scenario in seed_scenario_specs().values()
            if scenario.product_adapter_id == adapter.adapter_id
        ]
    seed = seed_scenario_specs()
    return [seed[item] for item in scenario_ids if item in seed]


def _gate_results_from_metadata(metadata: dict[str, Any]) -> list[ValidationGateResult]:
    raw = metadata.get("validation_gates") or metadata.get("validation_gate_results")
    if not isinstance(raw, list):
        return []
    return [
        ValidationGateResult.model_validate(item)
        for item in raw
        if isinstance(item, dict)
    ]


def _empty_resolution() -> CapabilityPackResolution:
    return CapabilityPackResolution(
        evidence_index=EvidenceArtifactIndex(index_id="capability-pack:not-selected"),
        redacted_metadata={
            "selected": False,
            "no_runtime_behavior_change": True,
        },
    )


def _default_adapter_id(pack: HarnessCapabilityPack) -> str:
    return {
        "excel_ai": "excel_ai",
        "chat_demo": "chat_demo",
    }.get(pack.target_product_family, "")


def _default_scenario_ids(adapter_id: str) -> list[str]:
    return [
        scenario.scenario_id
        for scenario in seed_scenario_specs().values()
        if scenario.product_adapter_id == adapter_id
    ]


def _dict_value(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _merge_unique(*groups: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for group in groups:
        for item in group:
            if item not in seen:
                seen.add(item)
                result.append(item)
    return result


__all__ = [
    "build_capability_pack_resolution",
    "build_capability_pack_dry_run",
    "resolve_capability_pack",
    "seed_adapter_manifests",
    "seed_capability_packs",
    "seed_deep_research_chat_demo_pack",
    "seed_excel_workbook_chat_pack",
    "seed_scenario_specs",
]
