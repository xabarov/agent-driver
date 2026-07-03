"""Deterministic provider plugin/catalog/routing tests."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts.provider_catalog import (
    ProviderCompatibilityReport,
    ProviderPluginManifest,
)
from agent_driver.llm.provider_catalog import (
    ProviderPluginRegistry,
    bridge_provider_descriptor,
    build_provider_compatibility_report,
    build_provider_routing_plan,
    seed_provider_capability_matrix,
    seed_provider_plugin_manifests,
    seed_provider_plugin_registry,
    seed_provider_sanitizer_fixtures,
    write_provider_catalog_artifacts,
)
from agent_driver.llm.provider_descriptors import get_provider_descriptor


def test_provider_plugin_manifest_rejects_secret_values_and_raw_responses() -> None:
    with pytest.raises(ValueError, match="must not contain secret values"):
        ProviderPluginManifest(
            plugin_id="bad",
            version="0.1.0",
            provider_ids=["bad"],
            transports=["openai_compatible"],
            redacted_metadata={"api_key": "sk-live-secret"},
        )

    with pytest.raises(ValueError, match="must not contain raw provider responses"):
        ProviderPluginManifest(
            plugin_id="bad_raw",
            version="0.1.0",
            provider_ids=["bad"],
            transports=["openai_compatible"],
            redacted_metadata={"raw_response": {"id": "provider-response"}},
        )


def test_builtin_registry_resolves_aliases_and_rejects_duplicates() -> None:
    registry = seed_provider_plugin_registry()

    assert registry.get("openrouter").plugin_id == "openrouter"
    assert registry.get("zai").plugin_id == "glm_zai"
    assert registry.get("gemini").plugin_id == "gemma_gemini"

    duplicate = seed_provider_plugin_manifests()[0]
    with pytest.raises(ValueError, match="already registered"):
        registry.register(duplicate)

    replacement = duplicate.model_copy(update={"compatibility_notes": ["new"]})
    registry.register(replacement, replace_existing=True)
    assert registry.get("openai").compatibility_notes == ["new"]


def test_descriptor_bridge_includes_route_profile_without_secrets() -> None:
    bridge = bridge_provider_descriptor(
        get_provider_descriptor("openrouter"),
        model="openai/gpt-5.5",
    )

    assert bridge["descriptor"]["provider_id"] == "openrouter"
    assert bridge["descriptor"]["api_key_env"] == [
        "AGENT_DRIVER_API_KEY",
        "OPENROUTER_API_KEY",
        "LLM_API_KEY",
    ]
    assert bridge["route_profile"]["provider_id"] == "openrouter"
    assert "sk-" not in json.dumps(bridge).lower()


def test_sanitizer_matrix_has_accept_downgrade_block_and_no_claim() -> None:
    fixtures = seed_provider_sanitizer_fixtures()
    verdicts = {fixture.verdict for fixture in fixtures}
    statuses = {fixture.status for fixture in fixtures}

    assert {"accept", "downgrade", "block", "no_claim"} <= verdicts
    assert {"supported", "degraded", "blocked", "no_claim"} <= statuses
    openrouter_forced = next(
        fixture
        for fixture in fixtures
        if fixture.provider_id == "openrouter"
        and fixture.feature == "forced_tool_choice"
    )
    assert openrouter_forced.status == "degraded"
    assert "forced_tool_choice" in openrouter_forced.downgrades


def test_routing_plan_selects_first_viable_route_and_keeps_switching_opt_in() -> None:
    plan = build_provider_routing_plan(
        plan_id="test.plan",
        requested_capabilities=["tools", "strict_json_schema", "vision"],
        matrix=seed_provider_capability_matrix(),
        preferred_provider_ids=["openrouter", "openai"],
    )

    assert plan.selected_provider_id in {"openrouter", "openai"}
    assert plan.status in {"supported", "degraded"}
    assert "automatic_route_switching_opt_in" in plan.constraints
    assert plan.fallback_eligible is True


def test_provider_compatibility_report_and_artifacts_are_redaction_safe(tmp_path) -> None:
    report = build_provider_compatibility_report()

    dumped = report.model_dump(mode="json")
    assert dumped["status"] == "supported"
    assert dumped["live_status"] == "no_claim"
    assert {manifest["plugin_id"] for manifest in dumped["manifests"]} >= {
        "openai",
        "openrouter",
        "vllm",
        "anthropic",
        "deepseek",
        "glm_zai",
        "gemma_gemini",
        "ollama",
    }
    ProviderCompatibilityReport.model_validate(dumped)

    written = write_provider_catalog_artifacts(tmp_path, report)
    assert (tmp_path / "provider_compatibility_report.json").is_file()
    assert (tmp_path / "provider_compatibility_report.md").is_file()
    assert (tmp_path / "provider_catalog.json").is_file()
    assert (tmp_path / "provider_sanitizer_matrix.json").is_file()
    assert (tmp_path / "evidence_index.json").is_file()
    assert "provider_compatibility_report" in written
