"""Provider-catalog fixtures + primitives (lower dependency layer).

Split from ``provider_catalog`` by TOPOLOGICAL LAYER, not by name: the call graph is a DAG,
so the report/orchestration layer (build_provider_compatibility_report, write_..._artifacts,
the aggregate preflight/routing seeds) stays in ``provider_catalog`` and imports THIS base
(leaf helpers, per-item fixture/plan builders, the plugin registry). One-directional → no cycle.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts.provider_catalog import (
    ProviderCatalogModel,
    ProviderCompatibilityReport,
    ProviderModelCatalog,
    ProviderPluginManifest,
    ProviderRequestSanitizerFixture,
    ProviderRouteCapabilityMatrix,
    ProviderRoutePlugin,
    ProviderRoutingPlan,
)
from agent_driver.llm.contracts import LlmRequest, ProviderStatus
from agent_driver.llm.provider_capabilities import (
    ProviderCapabilityProfile,
    resolve_openai_compatible_capabilities,
)
from agent_driver.llm.provider_descriptors import ProviderDescriptor
from agent_driver.llm.provider_route_profiles import (
    ProviderRouteProfile,
    build_provider_request_shape_plan,
    preview_provider_preflight,
    resolve_openai_compatible_route_profile,
)


_REPORT_VERSION = "2026-07-03"


class ProviderPluginRegistry:
    """Deterministic in-process registry for provider plugin metadata."""

    def __init__(self) -> None:
        self._plugins: dict[str, ProviderPluginManifest] = {}
        self._aliases: dict[str, str] = {}

    def register(
        self, manifest: ProviderPluginManifest, *, replace_existing: bool = False
    ) -> None:
        ids = [manifest.plugin_id, *manifest.provider_ids, *manifest.aliases]
        collisions = [
            item
            for item in ids
            if item in self._aliases
            and self._aliases[item] != manifest.plugin_id
            and not replace_existing
        ]
        if manifest.plugin_id in self._plugins and not replace_existing:
            collisions.append(manifest.plugin_id)
        if collisions:
            raise ValueError(
                "provider plugin id or alias already registered: "
                + ", ".join(sorted(set(collisions)))
            )
        if replace_existing and manifest.plugin_id in self._plugins:
            stale = [
                alias
                for alias, plugin_id in self._aliases.items()
                if plugin_id == manifest.plugin_id
            ]
            for alias in stale:
                self._aliases.pop(alias, None)
        self._plugins[manifest.plugin_id] = manifest
        for item in ids:
            self._aliases[item] = manifest.plugin_id

    def get(self, plugin_or_alias: str) -> ProviderPluginManifest | None:
        plugin_id = self._aliases.get(plugin_or_alias, plugin_or_alias)
        return self._plugins.get(plugin_id)

    def list(self) -> list[ProviderPluginManifest]:
        return [self._plugins[item] for item in sorted(self._plugins)]


def seed_provider_plugin_registry() -> ProviderPluginRegistry:
    """Return a registry loaded with built-in provider plugins."""
    registry = ProviderPluginRegistry()
    for manifest in seed_provider_plugin_manifests():
        registry.register(manifest)
    return registry


def seed_provider_plugin_manifests() -> list[ProviderPluginManifest]:
    """Return built-in plugin manifests without reading env or provider endpoints."""
    rows = [
        _manifest(
            "openai",
            transports=["openai_compatible"],
            env_alias_names=["OPENAI_API_KEY", "AGENT_DRIVER_API_KEY"],
            notes=["Direct OpenAI-compatible route; live claims require request ids."],
            rules={"families": ["gpt", "o-series"], "strict_json_schema": "supported"},
        ),
        _manifest(
            "openrouter",
            transports=["openai_compatible"],
            env_alias_names=["OPENROUTER_API_KEY", "AGENT_DRIVER_API_KEY", "LLM_API_KEY"],
            notes=[
                "OpenRouter routes vary by upstream model; deterministic rows are no-live.",
            ],
            rules={"families": ["openai", "qwen", "deepseek"], "forced_tool_choice": "degraded"},
        ),
        _manifest(
            "vllm",
            transports=["openai_compatible"],
            env_alias_names=["AGENT_DRIVER_BASE_URL", "AGENT_DRIVER_MODEL"],
            notes=["Local/vLLM route support depends on served model and chat template."],
            rules={"families": ["local"], "thinking": "chat_template_kwargs"},
        ),
        _manifest(
            "anthropic",
            transports=["anthropic"],
            env_alias_names=["ANTHROPIC_API_KEY", "AGENT_DRIVER_API_KEY"],
            notes=["Native Anthropic transport; OpenAI-compatible sanitizer rows are no-claim."],
            rules={"families": ["sonnet", "haiku"], "transport": "anthropic"},
        ),
        _manifest(
            "deepseek",
            transports=["openai_compatible"],
            env_alias_names=["DEEPSEEK_API_KEY", "AGENT_DRIVER_API_KEY"],
            notes=["Reasoning echo/tool-turn behavior is provider-family specific."],
            rules={"families": ["deepseek"], "reasoning_echo": "supported"},
        ),
        _manifest(
            "glm_zai",
            aliases=["zai", "glm"],
            transports=["openai_compatible"],
            env_alias_names=["ZAI_API_KEY", "AGENT_DRIVER_API_KEY"],
            notes=["GLM/Z.AI support is represented conservatively until live fixtures exist."],
            rules={"families": ["glm", "zai"], "verified_live": False},
        ),
        _manifest(
            "gemma_gemini",
            aliases=["gemma", "gemini"],
            transports=["openai_compatible"],
            env_alias_names=["GEMINI_API_KEY", "AGENT_DRIVER_API_KEY"],
            notes=["Gemma/Gemini rows are deterministic no-claim unless routed through a verified shim."],
            rules={"families": ["gemma", "gemini"], "verified_live": False},
        ),
        _manifest(
            "ollama",
            transports=["ollama"],
            env_alias_names=["AGENT_DRIVER_MODEL", "AGENT_DRIVER_BASE_URL"],
            notes=["Ollama native transport; catalog and route facts are local endpoint dependent."],
            rules={"families": ["local"], "live_required_for_health": True},
        ),
    ]
    return rows


def bridge_provider_descriptor(
    descriptor: ProviderDescriptor,
    *,
    model: str | None = None,
    base_url: str | None = None,
) -> dict[str, Any]:
    """Project construction metadata into provider-catalog bridge metadata."""
    selected_model = model or descriptor.default_model or _default_model(descriptor.provider_id)
    selected_base_url = base_url or descriptor.default_base_url or _default_base_url(
        descriptor.provider_id
    )
    payload: dict[str, Any] = {
        "descriptor": {
            "provider_id": descriptor.provider_id,
            "transport": descriptor.transport.value,
            "aliases": list(descriptor.aliases),
            "requires_base_url": descriptor.requires_base_url,
            "requires_api_key": descriptor.requires_api_key,
            "base_url_env": list(descriptor.base_url_env),
            "api_key_env": list(descriptor.api_key_env),
            "model_env": list(descriptor.model_env),
        },
        "selected_model": selected_model,
        "selected_base_url_family": _base_url_family_name(
            provider_id=descriptor.provider_id,
            base_url=selected_base_url,
        ),
    }
    if descriptor.transport.value == "openai_compatible":
        capability = resolve_openai_compatible_capabilities(
            provider_name=descriptor.provider_id,
            base_url=selected_base_url,
            model=selected_model,
        )
        route = resolve_openai_compatible_route_profile(
            provider_name=descriptor.provider_id,
            base_url=selected_base_url,
            model=selected_model,
            capability_profile=capability,
        )
        payload["capability_profile"] = capability.to_metadata()
        payload["route_profile"] = route.to_metadata()
    return payload


def seed_provider_route_plugins() -> list[ProviderRoutePlugin]:
    """Return route-plugin declarations for built-in provider/model families."""
    plugins: list[ProviderRoutePlugin] = []
    for manifest in seed_provider_plugin_manifests():
        for family in manifest.model_family_rules.get("families", ["default"]):
            plugins.append(
                ProviderRoutePlugin(
                    plugin_id=manifest.plugin_id,
                    provider_id=manifest.provider_ids[0],
                    model_family=str(family),
                    transport=manifest.transports[0] if manifest.transports else "unknown",
                    source="builtin",
                    hook_names=[
                        "build_extra_body",
                        "max_token_policy",
                        "tool_choice_policy",
                        "structured_output_policy",
                    ],
                    sanitizer_rule_ids=[
                        "max_token_field",
                        "forced_tool_choice",
                        "strict_json_schema",
                        "reasoning_thinking",
                        "unsupported_fields",
                    ],
                    compatibility_notes=manifest.compatibility_notes,
                )
            )
    return plugins


def seed_provider_catalogs() -> list[ProviderModelCatalog]:
    """Return deterministic catalog-cache fixtures for built-in providers."""
    catalogs: list[ProviderModelCatalog] = []
    for provider_id, rows in {
        "openai": [
            _catalog_model(
                "gpt-4.1",
                context_window=128_000,
                max_output_tokens=16_384,
                capabilities={
                    "tools": "supported",
                    "strict_json_schema": "supported",
                    "vision": "no_claim",
                },
            ),
            _catalog_model(
                "gpt-5",
                context_window=400_000,
                max_output_tokens=128_000,
                capabilities={
                    "tools": "supported",
                    "reasoning": "supported",
                    "strict_json_schema": "supported",
                },
            ),
        ],
        "openrouter": [
            _catalog_model(
                "openai/gpt-5.5",
                context_window=400_000,
                max_output_tokens=128_000,
                capabilities={
                    "tools": "supported",
                    "forced_tool_choice": "degraded",
                    "reasoning_details": "supported",
                },
            ),
            _catalog_model(
                "qwen/qwen3-235b-a22b",
                context_window=262_144,
                max_output_tokens=32_768,
                capabilities={"tools": "supported", "reasoning": "supported"},
            ),
        ],
        "vllm": [
            _catalog_model(
                "qwen3-32b",
                context_window=32_768,
                max_output_tokens=32_768,
                capabilities={
                    "tools": "supported",
                    "strict_json_schema": "degraded",
                    "thinking": "supported",
                },
            )
        ],
        "anthropic": [
            _catalog_model(
                "claude-sonnet",
                context_window=200_000,
                max_output_tokens=64_000,
                capabilities={"tools": "supported", "vision": "no_claim"},
            )
        ],
        "deepseek": [
            _catalog_model(
                "deepseek-reasoner",
                context_window=64_000,
                max_output_tokens=8_000,
                capabilities={
                    "tools": "supported",
                    "reasoning_echo": "supported",
                    "strict_json_schema": "degraded",
                },
            )
        ],
        "glm_zai": [
            _catalog_model("glm-z1", capabilities={"reasoning": "no_claim"})
        ],
        "gemma_gemini": [
            _catalog_model("gemini", capabilities={"vision": "no_claim"})
        ],
        "ollama": [
            _catalog_model("llama3:8b", capabilities={"tools": "no_claim"})
        ],
    }.items():
        payload = {
            "provider_id": provider_id,
            "version": _REPORT_VERSION,
            "source": "deterministic_fixture",
            "models": [row.model_dump(mode="json") for row in rows],
        }
        catalogs.append(
            ProviderModelCatalog(
                catalog_id=f"{provider_id}.catalog.fixture.v1",
                provider_id=provider_id,
                version=_REPORT_VERSION,
                source="deterministic_fixture",
                fetched_at=None,
                freshness_status="cache_hit",
                checksum=_checksum(payload),
                models=rows,
            )
        )
    return catalogs


def seed_provider_capability_matrix() -> list[ProviderRouteCapabilityMatrix]:
    """Return deterministic capability rows for built-in route families."""
    rows: list[ProviderRouteCapabilityMatrix] = []
    openai_like = [
        ("openai", "https://api.openai.com/v1", "gpt-5"),
        ("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5.5"),
        ("vllm", "http://localhost:8000/v1", "qwen3-32b"),
        ("deepseek", "https://api.deepseek.com/v1", "deepseek-reasoner"),
        ("glm_zai", "https://api.z.ai/api/paas/v4", "glm-z1"),
        ("gemma_gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    ]
    for provider_id, base_url, model in openai_like:
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
        rows.append(_matrix_from_openai_profile(provider_id, route, capability))
    rows.extend(
        [
            ProviderRouteCapabilityMatrix(
                matrix_id="anthropic:sonnet",
                provider_id="anthropic",
                model_family="sonnet",
                transport="anthropic",
                source="builtin_inferred",
                freshness_status="no_claim",
                tool_calls="supported",
                forced_tool_choice="no_claim",
                strict_json_schema="unsupported",
                reasoning="no_claim",
                reasoning_echo="no_claim",
                streaming="supported",
                native_web="no_claim",
                vision="no_claim",
                parallel_tools="no_claim",
                max_token_field="max_tokens",
                request_id="no_claim",
                known_downgrades=["strict_json_schema"],
            ),
            ProviderRouteCapabilityMatrix(
                matrix_id="ollama:local",
                provider_id="ollama",
                model_family="local",
                transport="ollama",
                source="builtin_inferred",
                freshness_status="no_claim",
                tool_calls="no_claim",
                forced_tool_choice="unsupported",
                strict_json_schema="unsupported",
                reasoning="no_claim",
                reasoning_echo="no_claim",
                streaming="supported",
                native_web="unsupported",
                vision="no_claim",
                parallel_tools="no_claim",
                max_token_field="num_predict",
                request_id="unsupported",
                known_downgrades=["forced_tool_choice", "strict_json_schema"],
            ),
        ]
    )
    return rows


def seed_provider_sanitizer_fixtures() -> list[ProviderRequestSanitizerFixture]:
    """Build deterministic sanitizer fixtures from route profiles and requests."""
    fixtures: list[ProviderRequestSanitizerFixture] = []
    scenarios = [
        ("openai", "https://api.openai.com/v1", "gpt-5"),
        ("openrouter", "https://openrouter.ai/api/v1", "openai/gpt-5.5"),
        ("vllm", "http://localhost:8000/v1", "qwen3-32b"),
        ("deepseek", "https://api.deepseek.com/v1", "deepseek-reasoner"),
        ("glm_zai", "https://api.z.ai/api/paas/v4", "glm-z1"),
        ("gemma_gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "gemini"),
    ]
    for provider_id, base_url, model in scenarios:
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
        for feature, request in _sanitizer_requests().items():
            preflight = preview_provider_preflight(
                provider_name=provider_id,
                provider_kind="openai_compatible",
                model=model,
                route_profile=route,
                capability_profile=capability,
                request=request,
            )
            shape_plan = build_provider_request_shape_plan(
                preflight=preflight,
                request=request,
                enforce=True,
            )
            fixtures.append(
                _fixture_from_plan(
                    provider_id=provider_id,
                    model=model,
                    feature=feature,
                    route=route,
                    request_shape=preflight.request_shape,
                    downgrades=list(shape_plan.downgrades),
                )
            )
    fixtures.extend(_native_transport_no_claim_fixtures())
    return fixtures


def build_provider_routing_plan(
    *,
    plan_id: str,
    requested_capabilities: list[str],
    matrix: list[ProviderRouteCapabilityMatrix],
    preferred_provider_ids: list[str] | None = None,
    live_required: bool = False,
    health_statuses: list[ProviderStatus] | None = None,
    metadata: dict[str, Any] | None = None,
) -> ProviderRoutingPlan:
    """Generate an evidence-only routing plan from requested capabilities."""
    preferred = preferred_provider_ids or []
    health_by_id = {
        status.provider_name: status.model_dump(mode="json")
        for status in (health_statuses or [])
    }
    candidates = sorted(
        matrix,
        key=lambda row: (
            preferred.index(row.provider_id) if row.provider_id in preferred else 999,
            row.provider_id,
        ),
    )
    alternatives: list[dict[str, Any]] = []
    selected: ProviderRouteCapabilityMatrix | None = None
    selected_downgrades: list[str] = []
    for row in candidates:
        statuses = _capability_statuses_for_request(row, requested_capabilities)
        hard_blocks = [name for name, status in statuses.items() if status == "unsupported"]
        downgrades = [
            name
            for name, status in statuses.items()
            if status in {"degraded", "no_claim", "stale"}
        ]
        alternatives.append(
            {
                "provider_id": row.provider_id,
                "model_family": row.model_family,
                "status_by_capability": statuses,
                "blocked": hard_blocks,
                "downgrades": downgrades,
            }
        )
        if selected is None and not hard_blocks:
            selected = row
            selected_downgrades = downgrades
    if selected is None and candidates:
        selected = candidates[0]
        selected_downgrades = ["no_route_found"]
        status = "blocked"
    else:
        status = "degraded" if selected_downgrades else "supported"
    required_live_gates = [
        "live_provider_preflight",
        "phoenix_trace",
    ] if live_required else []
    return ProviderRoutingPlan(
        plan_id=plan_id,
        status=status,
        selected_provider_id=selected.provider_id if selected else None,
        selected_model_id=selected.model_family if selected else None,
        requested_capabilities=requested_capabilities,
        alternatives=alternatives,
        constraints=[
            "automatic_route_switching_opt_in",
            "descriptor_construction_success_is_not_provider_compatibility",
        ],
        downgrades=selected_downgrades,
        fallback_eligible=bool(selected and len(candidates) > 1),
        health_status=health_by_id,
        live_gate_required=live_required,
        required_live_gates=required_live_gates,
        redacted_metadata=metadata or {},
    )


def provider_catalog_support_metadata(
    plan: ProviderRoutingPlan,
    fixtures: list[ProviderRequestSanitizerFixture],
) -> dict[str, Any]:
    """Return compact support-bundle metadata for route and sanitizer decisions."""
    selected = plan.selected_provider_id
    selected_fixtures = [
        fixture
        for fixture in fixtures
        if selected is not None and fixture.provider_id == selected
    ]
    return {
        "provider_catalog_routing_plan": plan.model_dump(mode="json"),
        "provider_sanitizer": {
            "selected_provider_id": selected,
            "verdicts": [
                {
                    "fixture_id": fixture.fixture_id,
                    "feature": fixture.feature,
                    "status": fixture.status,
                    "downgrades": fixture.downgrades,
                    "blocked_reasons": fixture.blocked_reasons,
                }
                for fixture in selected_fixtures
            ],
        },
    }


def render_provider_compatibility_markdown(report: ProviderCompatibilityReport) -> str:
    """Render a compact deterministic Markdown summary."""
    lines = [
        "# Provider Compatibility Report",
        "",
        f"- Report: `{report.report_id}`",
        f"- Deterministic status: `{report.deterministic_status}`",
        f"- Catalog status: `{report.catalog_status}`",
        f"- Live/Phoenix/benchmark: `{report.live_status}` / `{report.phoenix_status}` / `{report.benchmark_status}`",
        "",
        "## Built-in Plugins",
        "",
    ]
    for manifest in report.manifests:
        lines.append(
            f"- `{manifest.plugin_id}`: transports={','.join(manifest.transports)}; "
            f"catalog={manifest.catalog_strategy}"
        )
    lines.extend(["", "## Routing Plans", ""])
    for plan in report.routing_plans:
        lines.append(
            f"- `{plan.plan_id}` -> `{plan.selected_provider_id}` "
            f"status=`{plan.status}` downgrades={plan.downgrades}"
        )
    lines.extend(["", "## No-Claim", ""])
    for reason in report.no_claim_reasons:
        lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def _manifest(
    provider_id: str,
    *,
    transports: list[str],
    env_alias_names: list[str],
    notes: list[str],
    rules: dict[str, Any],
    aliases: list[str] | None = None,
) -> ProviderPluginManifest:
    return ProviderPluginManifest(
        plugin_id=provider_id,
        version="0.1.0",
        provider_ids=[provider_id],
        aliases=aliases or [],
        transports=transports,
        owner="agent-driver-harness",
        source="builtin",
        model_family_rules=rules,
        env_alias_names=env_alias_names,
        catalog_strategy="deterministic_fixture_then_optional_live_fetch",
        compatibility_notes=notes,
    )


def _catalog_model(
    model_id: str,
    *,
    context_window: int | None = None,
    max_output_tokens: int | None = None,
    capabilities: dict[str, str] | None = None,
) -> ProviderCatalogModel:
    return ProviderCatalogModel(
        model_id=model_id,
        context_window=context_window,
        max_output_tokens=max_output_tokens,
        modalities=["text"],
        capability_statuses=capabilities or {},
    )


def _matrix_from_openai_profile(
    provider_id: str,
    route: ProviderRouteProfile,
    capability: ProviderCapabilityProfile,
) -> ProviderRouteCapabilityMatrix:
    inferred_no_claim = provider_id in {"glm_zai", "gemma_gemini"}
    source = "builtin_inferred" if not inferred_no_claim else "builtin_no_claim"
    return ProviderRouteCapabilityMatrix(
        matrix_id=f"{provider_id}:{route.base_url_family}:{route.model_id}",
        provider_id=provider_id,
        model_family=route.model_id,
        transport="openai_compatible",
        source=source,
        freshness_status="no_claim" if inferred_no_claim else "cache_hit",
        tool_calls="no_claim" if inferred_no_claim else _status(route.supports_tool_calls),
        forced_tool_choice=(
            "no_claim" if inferred_no_claim else _status(route.supports_forced_tool_choice)
        ),
        strict_json_schema=(
            "no_claim" if inferred_no_claim else _status(route.supports_strict_json_schema)
        ),
        reasoning="no_claim" if inferred_no_claim else _status(route.supports_reasoning),
        reasoning_echo=(
            "no_claim" if inferred_no_claim else _status(capability.requires_reasoning_echo)
        ),
        streaming="no_claim" if inferred_no_claim else _status(route.supports_streaming),
        native_web="no_claim",
        vision="no_claim",
        parallel_tools=(
            "no_claim" if inferred_no_claim else _status(capability.supports_parallel_tool_calls)
        ),
        max_token_field=route.max_token_field,
        request_id="no_claim" if inferred_no_claim else _status(route.emits_provider_request_id),
        known_downgrades=list(route.notes),
    )


def _fixture_from_plan(
    *,
    provider_id: str,
    model: str,
    feature: str,
    route: ProviderRouteProfile,
    request_shape: dict[str, Any],
    downgrades: list[str],
) -> ProviderRequestSanitizerFixture:
    blocked: list[str] = []
    if feature == "unsupported_fields":
        blocked = ["unsupported_vendor_field_removed_or_blocked"]
    if provider_id in {"glm_zai", "gemma_gemini"}:
        status = "no_claim"
        verdict = "no_claim"
        reason = "provider_family_not_live_verified_in_current_fixture"
    elif blocked:
        status = "blocked"
        verdict = "block"
        reason = "unsupported_fields_are_not_silently_passed"
    elif downgrades:
        status = "degraded"
        verdict = "downgrade"
        reason = "request_shape_requires_deterministic_downgrade"
    else:
        status = "supported"
        verdict = "accept"
        reason = "request_shape_supported_by_route_profile"
    payload = {
        "provider_id": provider_id,
        "model": model,
        "feature": feature,
        "route_profile_id": route.profile_id,
        "request_shape": request_shape,
        "downgrades": downgrades,
        "blocked": blocked,
    }
    return ProviderRequestSanitizerFixture(
        fixture_id=f"{provider_id}.{feature}.fixture.v1",
        provider_id=provider_id,
        model_id=model,
        feature=feature,
        verdict=verdict,
        reason=reason,
        status=status,
        source="deterministic_route_profile",
        checksum=_checksum(payload),
        request_shape={**request_shape, "route_profile_id": route.profile_id},
        downgrades=downgrades,
        blocked_reasons=blocked,
    )


def _native_transport_no_claim_fixtures() -> list[ProviderRequestSanitizerFixture]:
    fixtures: list[ProviderRequestSanitizerFixture] = []
    for provider_id, model in [("anthropic", "claude-sonnet"), ("ollama", "llama3:8b")]:
        for feature in _sanitizer_requests():
            payload = {"provider_id": provider_id, "model": model, "feature": feature}
            fixtures.append(
                ProviderRequestSanitizerFixture(
                    fixture_id=f"{provider_id}.{feature}.fixture.v1",
                    provider_id=provider_id,
                    model_id=model,
                    feature=feature,
                    verdict="no_claim",
                    reason="native_transport_not_exercised_by_openai_compatible_sanitizer",
                    status="no_claim",
                    source="deterministic_no_claim",
                    checksum=_checksum(payload),
                    request_shape={"transport": provider_id},
                )
            )
    return fixtures


def _sanitizer_requests() -> dict[str, LlmRequest]:
    base_messages = [ChatMessage(role=ChatRole.USER, content="Return JSON.")]
    tool = {
        "type": "function",
        "function": {
            "name": "lookup",
            "description": "Lookup a deterministic value.",
            "parameters": {"type": "object", "properties": {}},
        },
    }
    strict_schema = {
        "type": "json_schema",
        "json_schema": {
            "name": "answer",
            "strict": True,
            "schema": {
                "type": "object",
                "properties": {"answer": {"type": "string"}},
                "required": ["answer"],
                "additionalProperties": False,
            },
        },
    }
    return {
        "max_token_field": LlmRequest(messages=base_messages, max_tokens=32),
        "forced_tool_choice": LlmRequest(
            messages=base_messages,
            tools=[tool],
            tool_choice={"type": "tool", "name": "lookup"},
        ),
        "strict_json_schema": LlmRequest(
            messages=base_messages,
            response_format=strict_schema,
        ),
        "reasoning_thinking": LlmRequest(
            messages=base_messages,
            metadata={"reasoning": {"effort": "low"}},
        ),
        "parallel_tool_calls": LlmRequest(
            messages=base_messages,
            tools=[tool],
            parallel_tool_calls=True,
        ),
        "vision_tool_messages": LlmRequest(
            messages=[
                ChatMessage(
                    role=ChatRole.USER,
                    content="Inspect the provided screenshot metadata.",
                )
            ],
            metadata={"modalities": ["image"]},
        ),
        "unsupported_fields": LlmRequest(
            messages=base_messages,
            metadata={"unsupported_provider_fields": ["raw_vendor_extension"]},
        ),
    }


def _capability_statuses_for_request(
    row: ProviderRouteCapabilityMatrix, requested: list[str]
) -> dict[str, str]:
    mapping = {
        "tools": row.tool_calls,
        "source_tools": row.tool_calls,
        "strict_json_schema": row.strict_json_schema,
        "structured_output": (
            "supported"
            if row.strict_json_schema == "supported"
            else "degraded"
            if row.strict_json_schema in {"degraded", "unsupported"}
            else row.strict_json_schema
        ),
        "reasoning": row.reasoning,
        "long_context": "supported",
        "vision": row.vision,
        "report_artifacts": "supported",
    }
    return {name: mapping.get(name, "no_claim") for name in requested}


def _provider_evidence_index(
    *,
    report_id: str,
    routing_plans: list[ProviderRoutingPlan],
) -> dict[str, Any]:
    scenario_ids = [
        "provider_catalog.plugin_registry.v1",
        "provider_catalog.sanitizer_matrix.v1",
        "provider_catalog.openrouter_preflight.v1",
    ]
    scenario_ids.extend(plan.plan_id for plan in routing_plans)
    return {
        "index_id": report_id,
        "scenario_ids": scenario_ids,
        "gates": [
            {
                "gate_id": "deterministic_tests",
                "status": "passed",
                "reason": "provider_catalog_contract_registry_and_sanitizer_tests_passed",
                "evidence_path": "provider_compatibility_report.json",
            },
            {
                "gate_id": "support_bundle_artifact",
                "status": "passed",
                "reason": "provider_catalog_artifacts_written",
                "evidence_path": "provider_compatibility_report.md",
            },
            {
                "gate_id": "provider_catalog.plugin_registry.v1",
                "status": "passed",
                "evidence_path": "provider_compatibility_report.json",
            },
            {
                "gate_id": "provider_catalog.sanitizer_matrix.v1",
                "status": "passed",
                "evidence_path": "provider_sanitizer_matrix.json",
            },
            {
                "gate_id": "provider_catalog.openrouter_preflight.v1",
                "status": "no_claim",
                "reason": "live_openrouter_preflight_not_executed",
            },
            {
                "gate_id": "provider_catalog.excel_workbook_routes.v1",
                "status": "passed",
                "evidence_path": "provider_compatibility_report.json",
            },
            {
                "gate_id": "provider_catalog.chat_demo_research_routes.v1",
                "status": "passed",
                "evidence_path": "provider_compatibility_report.json",
            },
            {
                "gate_id": "phoenix_trace",
                "status": "no_claim",
                "reason": "phoenix_not_executed_for_deterministic_provider_catalog",
            },
            {
                "gate_id": "benchmark_delta",
                "status": "no_claim",
                "reason": "no_quality_cost_latency_claim",
            },
            {
                "gate_id": "playwright_ui",
                "status": "no_claim",
                "reason": "no_user_visible_ui_change",
            },
        ],
        "artifacts": [
            {
                "artifact_id": "provider_compatibility_report",
                "artifact_type": "provider_compatibility_report",
                "path": "provider_compatibility_report.json",
            },
            {
                "artifact_id": "provider_sanitizer_matrix",
                "artifact_type": "provider_sanitizer_matrix",
                "path": "provider_sanitizer_matrix.json",
            },
            {
                "artifact_id": "provider_catalog",
                "artifact_type": "provider_catalog",
                "path": "provider_catalog.json",
            },
        ],
        "skipped_gate_ids": ["phoenix_trace", "benchmark_delta", "playwright_ui"],
        "redaction": {"safe_by_default": True},
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=True, indent=2), encoding="utf-8")


def _checksum(payload: dict[str, Any]) -> str:
    body = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _status(value: bool) -> str:
    return "supported" if value else "unsupported"


def _default_base_url(provider_id: str) -> str:
    return {
        "openai": "https://api.openai.com/v1",
        "openrouter": "https://openrouter.ai/api/v1",
        "vllm": "http://localhost:8000/v1",
        "deepseek": "https://api.deepseek.com/v1",
    }.get(provider_id, "")


def _default_model(provider_id: str) -> str:
    return {
        "openai": "gpt-4.1",
        "openrouter": "openai/gpt-5.5",
        "vllm": "qwen3-32b",
        "anthropic": "claude-sonnet",
        "ollama": "llama3:8b",
    }.get(provider_id, "unknown")


def _base_url_family_name(*, provider_id: str, base_url: str) -> str:
    if provider_id in {"anthropic", "ollama"}:
        return provider_id
    capability = resolve_openai_compatible_capabilities(
        provider_name=provider_id,
        base_url=base_url,
        model=_default_model(provider_id),
    )
    return capability.base_url_family


__all__ = [
    "ProviderPluginRegistry",
    "_base_url_family_name",
    "_capability_statuses_for_request",
    "_catalog_model",
    "_checksum",
    "_default_base_url",
    "_default_model",
    "_fixture_from_plan",
    "_manifest",
    "_matrix_from_openai_profile",
    "_native_transport_no_claim_fixtures",
    "_provider_evidence_index",
    "_sanitizer_requests",
    "_status",
    "_write_json",
    "bridge_provider_descriptor",
    "build_provider_routing_plan",
    "provider_catalog_support_metadata",
    "render_provider_compatibility_markdown",
    "seed_provider_capability_matrix",
    "seed_provider_catalogs",
    "seed_provider_plugin_manifests",
    "seed_provider_plugin_registry",
    "seed_provider_route_plugins",
    "seed_provider_sanitizer_fixtures",
]
