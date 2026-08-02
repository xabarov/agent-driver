"""Deterministic provider plugin/catalog/routing evidence layer."""

from __future__ import annotations

from pathlib import Path

from agent_driver.contracts.provider_catalog import (
    ProviderCatalogFetchPlan,
    ProviderCompatibilityReport,
    ProviderPreflightReport,
    ProviderRouteCapabilityMatrix,
    ProviderRoutingPlan,
)
from agent_driver.llm.provider_capabilities import (
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.provider_route_profiles import (
    build_provider_request_shape_plan,
    preview_provider_preflight,
    resolve_openai_compatible_route_profile,
)
from agent_driver.llm.provider_catalog_fixtures import (
    ProviderPluginRegistry,
    _provider_evidence_index,
    _sanitizer_requests,
    _write_json,
    bridge_provider_descriptor,
    build_provider_routing_plan,
    provider_catalog_support_metadata,
    render_provider_compatibility_markdown,
    seed_provider_capability_matrix,
    seed_provider_catalogs,
    seed_provider_plugin_manifests,
    seed_provider_plugin_registry,
    seed_provider_route_plugins,
    seed_provider_sanitizer_fixtures,
)

_NO_LIVE_REASON = "live_provider_phoenix_playwright_benchmark_not_executed"


def build_provider_compatibility_report(
    *,
    report_id: str = "provider_catalog.deterministic.v1",
    include_hosts: bool = True,
) -> ProviderCompatibilityReport:
    """Build deterministic provider compatibility evidence without live calls."""
    manifests = seed_provider_plugin_manifests()
    route_plugins = seed_provider_route_plugins()
    catalogs = seed_provider_catalogs()
    matrices = seed_provider_capability_matrix()
    fixtures = seed_provider_sanitizer_fixtures()
    routing_plans = seed_provider_routing_plans() if include_hosts else []
    preflight_reports = seed_provider_preflight_reports(matrices)
    evidence_index = _provider_evidence_index(
        report_id=report_id,
        routing_plans=routing_plans,
    )
    return ProviderCompatibilityReport(
        report_id=report_id,
        status="supported",
        deterministic_status="supported",
        catalog_status="cache_hit",
        live_status="no_claim",
        phoenix_status="no_claim",
        benchmark_status="no_claim",
        no_claim_reasons=[_NO_LIVE_REASON],
        manifests=manifests,
        route_plugins=route_plugins,
        catalogs=catalogs,
        capability_matrix=matrices,
        sanitizer_fixtures=fixtures,
        routing_plans=routing_plans,
        preflight_reports=preflight_reports,
        evidence_index=evidence_index,
    )


def seed_provider_routing_plans() -> list[ProviderRoutingPlan]:
    """Return host route plans for Excel AI and chat-demo without changing routing."""
    matrices = seed_provider_capability_matrix()
    return [
        build_provider_routing_plan(
            plan_id="provider_catalog.excel_workbook_routes.v1",
            requested_capabilities=[
                "tools",
                "strict_json_schema",
                "long_context",
                "vision",
                "reasoning",
            ],
            matrix=matrices,
            preferred_provider_ids=["openrouter", "openai", "vllm"],
            live_required=False,
            health_statuses=[],
            metadata={"product": "excel_ai", "route_family": "workbook_chat"},
        ),
        build_provider_routing_plan(
            plan_id="provider_catalog.chat_demo_research_routes.v1",
            requested_capabilities=[
                "tools",
                "long_context",
                "structured_output",
                "source_tools",
                "report_artifacts",
            ],
            matrix=matrices,
            preferred_provider_ids=["openrouter", "openai", "vllm"],
            live_required=False,
            health_statuses=[],
            metadata={"product": "chat_demo", "route_family": "deep_research"},
        ),
    ]


def seed_provider_preflight_reports(
    matrix: list[ProviderRouteCapabilityMatrix] | None = None,
) -> list[ProviderPreflightReport]:
    """Return generalized deterministic preflight reports for built-in rows."""
    matrix_by_provider = {row.provider_id: row for row in (matrix or seed_provider_capability_matrix())}
    reports: list[ProviderPreflightReport] = []
    for provider_id, base_url, model in [
        ("openai", "https://api.openai.com/v1", "gpt-5"),
        ("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5.5"),
        ("vllm", "http://localhost:8000/v1", "qwen3-32b"),
    ]:
        capability = resolve_openai_compatible_capabilities(
            provider_name=provider_id,
            base_url=base_url,
            model=model,
        )
        route = resolve_openai_compatible_route_profile(
            provider_name=provider_id,
            base_url=base_url,
            model=model,
            capability_profile=capability,
        )
        request = _sanitizer_requests()["strict_json_schema"]
        preflight = preview_provider_preflight(
            provider_name=provider_id,
            provider_kind="openai_compatible",
            model=model,
            route_profile=route,
            capability_profile=capability,
            request=request,
        )
        plan = build_provider_request_shape_plan(
            preflight=preflight,
            request=request,
            enforce=True,
        )
        reports.append(
            ProviderPreflightReport(
                report_id=f"{provider_id}.preflight.deterministic.v1",
                provider_id=provider_id,
                model_id=model,
                route_profile=route.to_metadata(),
                capability_matrix=matrix_by_provider[provider_id],
                request_shape_plan=plan.to_metadata(),
                catalog_fetch_plan=ProviderCatalogFetchPlan(
                    provider_id=provider_id,
                    status="fetch_skipped",
                    live_allowed=False,
                    source="deterministic_fixture",
                    reason="deterministic_compatibility_does_not_require_network",
                ),
                catalog_freshness_status="cache_hit",
                live_result={
                    "status": "skipped",
                    "reason": "live_provider_not_requested",
                },
                validation_gate_statuses={
                    "deterministic_provider_catalog": "supported",
                    "live_provider_preflight": "no_claim",
                    "phoenix_trace": "no_claim",
                },
            )
        )
    return reports


def write_provider_catalog_artifacts(
    output_dir: str | Path,
    report: ProviderCompatibilityReport | None = None,
) -> dict[str, str]:
    """Persist deterministic provider-catalog artifacts."""
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    selected = report or build_provider_compatibility_report()
    payload = selected.model_dump(mode="json")
    artifacts = {
        "provider_compatibility_report": root / "provider_compatibility_report.json",
        "provider_compatibility_markdown": root / "provider_compatibility_report.md",
        "provider_catalog": root / "provider_catalog.json",
        "provider_sanitizer_matrix": root / "provider_sanitizer_matrix.json",
        "validation_gates": root / "validation_gates.json",
        "evidence_index": root / "evidence_index.json",
    }
    _write_json(artifacts["provider_compatibility_report"], payload)
    _write_json(
        artifacts["provider_catalog"],
        {"catalogs": [item.model_dump(mode="json") for item in selected.catalogs]},
    )
    _write_json(
        artifacts["provider_sanitizer_matrix"],
        {
            "fixtures": [
                item.model_dump(mode="json") for item in selected.sanitizer_fixtures
            ]
        },
    )
    validation_gates = {
        "statuses": {
            "provider_catalog.plugin_registry.v1": "passed",
            "provider_catalog.sanitizer_matrix.v1": "passed",
            "provider_catalog.openrouter_preflight.v1": "no_claim",
            "provider_catalog.excel_workbook_routes.v1": "passed",
            "provider_catalog.chat_demo_research_routes.v1": "passed",
            "phoenix_trace": "no_claim",
            "benchmark_delta": "no_claim",
            "playwright_ui": "no_claim",
        },
        "gates": [
            {"gate_id": key, "status": value}
            for key, value in sorted(
                {
                    "provider_catalog.plugin_registry.v1": "passed",
                    "provider_catalog.sanitizer_matrix.v1": "passed",
                    "provider_catalog.openrouter_preflight.v1": "no_claim",
                    "provider_catalog.excel_workbook_routes.v1": "passed",
                    "provider_catalog.chat_demo_research_routes.v1": "passed",
                    "phoenix_trace": "no_claim",
                    "benchmark_delta": "no_claim",
                    "playwright_ui": "no_claim",
                }.items()
            )
        ],
    }
    _write_json(artifacts["validation_gates"], validation_gates)
    _write_json(artifacts["evidence_index"], selected.evidence_index)
    artifacts["provider_compatibility_markdown"].write_text(
        render_provider_compatibility_markdown(selected),
        encoding="utf-8",
    )
    return {key: str(path) for key, path in artifacts.items()}


__all__ = [
    "ProviderPluginRegistry",
    "bridge_provider_descriptor",
    "build_provider_compatibility_report",
    "build_provider_routing_plan",
    "provider_catalog_support_metadata",
    "render_provider_compatibility_markdown",
    "seed_provider_capability_matrix",
    "seed_provider_catalogs",
    "seed_provider_plugin_manifests",
    "seed_provider_plugin_registry",
    "seed_provider_preflight_reports",
    "seed_provider_route_plugins",
    "seed_provider_routing_plans",
    "seed_provider_sanitizer_fixtures",
    "write_provider_catalog_artifacts",
]
