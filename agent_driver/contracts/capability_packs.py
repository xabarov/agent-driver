"""Capability-pack contracts for productized harness validation."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.contracts.validation import (
    assert_no_secret_fields,
    ensure_json_serializable,
)

_PACK_STATUSES = frozenset({"draft", "experimental", "active", "deprecated", "blocked"})
_SCENARIO_STATUSES = frozenset(
    {
        "candidate",
        "deterministic",
        "host_validated",
        "live_validated",
        "benchmarked",
        "flaky",
        "retired",
    }
)
_GATE_CLASSES = frozenset(
    {
        "deterministic",
        "replay",
        "host_integration",
        "live_provider",
        "phoenix",
        "playwright",
        "benchmark",
    }
)
_ARTIFACT_TYPES = frozenset(
    {
        "support_bundle",
        "trace_summary",
        "phoenix_trace",
        "playwright_screenshot",
        "benchmark_json",
        "benchmark_markdown",
        "adapter_compatibility_report",
        "adapter_events",
        "lifecycle_hook_compatibility_report",
        "lifecycle_hook_audit",
        "provider_compatibility_report",
        "provider_catalog",
        "provider_sanitizer_matrix",
        "validation_gates",
        "skill_inventory_snapshot",
        "skill_lockfile",
        "skill_reload_diff",
        "skill_selection_evidence",
        "skill_lifecycle_compatibility_report",
        "mcp_registry_snapshot",
        "mcp_approval_evidence",
        "mcp_call_provenance",
        "mcp_governance_compatibility_report",
        "command_output",
        "cost_latency_record",
        "skip_justification",
        "other",
    }
)
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")


def _assert_no_secret_fields(value: Any) -> None:
    """Reject secret-shaped keys except when the value is an env-var name."""
    assert_no_secret_fields(
        value, subject="capability pack metadata", markers=_SECRET_KEY_MARKERS
    )


class HarnessReleaseGate(ContractModel):
    """Rule for accepting a scenario or pack-driven harness change."""

    gate_id: str
    gate_class: str
    required: bool = True
    command: str | None = None
    evidence_paths: list[str] = Field(default_factory=list)
    skip_condition: str | None = None
    max_cost_usd: float | None = None
    max_flake_budget: int | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("gate_class")
    @classmethod
    def validate_gate_class(cls, value: str) -> str:
        if value not in _GATE_CLASSES:
            raise ValueError(f"unsupported release gate class: {value}")
        return value

    @field_validator("max_cost_usd")
    @classmethod
    def validate_max_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("max_cost_usd must be >= 0")
        return value

    @field_validator("max_flake_budget")
    @classmethod
    def validate_flake_budget(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("max_flake_budget must be >= 0")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="release gate metadata")


class HarnessCapabilityPack(ContractModel):
    """Versioned product-neutral harness capability profile."""

    pack_id: str
    version: str
    target_product_family: str
    status: str = "draft"
    provider_route_requirements: dict[str, Any] = Field(default_factory=dict)
    policy_profile_defaults: dict[str, Any] = Field(default_factory=dict)
    supervision_expectations: dict[str, Any] = Field(default_factory=dict)
    required_evidence: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    context_requirements: list[str] = Field(default_factory=list)
    side_effect_classes: list[str] = Field(default_factory=list)
    release_gates: list[HarnessReleaseGate] = Field(default_factory=list)
    rollout_mode: str = "inert"
    compatibility: dict[str, Any] = Field(default_factory=dict)
    owners: list[str] = Field(default_factory=list)
    ownership_notes: list[str] = Field(default_factory=list)
    review_checklist: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _PACK_STATUSES:
            raise ValueError(f"unsupported capability pack status: {value}")
        return value

    @field_validator(
        "provider_route_requirements",
        "policy_profile_defaults",
        "supervision_expectations",
        "compatibility",
    )
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="capability pack metadata")

    @model_validator(mode="after")
    def validate_no_secret_values(self) -> "HarnessCapabilityPack":
        _assert_no_secret_fields(self.model_dump(mode="json"))
        return self


class HarnessScenarioSpec(ContractModel):
    """Stable scenario definition used by packs and adapters."""

    scenario_id: str
    status: str = "candidate"
    product_adapter_id: str
    prompt_seed: str | None = None
    task_seed: dict[str, Any] = Field(default_factory=dict)
    required_tools: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    expected_policy_verdicts: list[str] = Field(default_factory=list)
    expected_provenance_verdicts: list[str] = Field(default_factory=list)
    expected_supervisor_verdicts: list[str] = Field(default_factory=list)
    deterministic_gate_ids: list[str] = Field(default_factory=list)
    optional_live_gate_ids: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    skip_conditions: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _SCENARIO_STATUSES:
            raise ValueError(f"unsupported scenario status: {value}")
        return value

    @field_validator("task_seed", "redacted_metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="scenario metadata")


class HarnessAdapterManifest(ContractModel):
    """Host adapter declaration for pack dry-runs and evidence discovery."""

    adapter_id: str
    product_name: str
    product_family: str
    expected_ports: dict[str, int] = Field(default_factory=dict)
    env_var_names: list[str] = Field(default_factory=list)
    start_commands: list[str] = Field(default_factory=list)
    deterministic_commands: list[str] = Field(default_factory=list)
    optional_live_commands: list[str] = Field(default_factory=list)
    trace_endpoints: list[str] = Field(default_factory=list)
    support_bundle_endpoints: list[str] = Field(default_factory=list)
    benchmark_commands: list[str] = Field(default_factory=list)
    playwright_specs: list[str] = Field(default_factory=list)
    artifact_output_paths: list[str] = Field(default_factory=list)
    known_non_goals: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_ports")
    @classmethod
    def validate_ports(cls, value: dict[str, int]) -> dict[str, int]:
        for name, port in value.items():
            if port <= 0 or port > 65535:
                raise ValueError(f"invalid port for {name}: {port}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="adapter manifest metadata")


class EvidenceArtifactRef(ContractModel):
    """Portable reference to one produced validation artifact."""

    artifact_id: str
    artifact_type: str
    path: str | None = None
    uri: str | None = None
    sha256: str | None = None
    gate_id: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_type")
    @classmethod
    def validate_artifact_type(cls, value: str) -> str:
        if value not in _ARTIFACT_TYPES:
            raise ValueError(f"unsupported evidence artifact type: {value}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="artifact ref metadata")


class EvidenceArtifactIndex(ContractModel):
    """Machine-readable validation artifact index for a pack/scenario run."""

    index_id: str
    pack_id: str | None = None
    pack_version: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    gates: list[ValidationGateResult] = Field(default_factory=list)
    artifacts: list[EvidenceArtifactRef] = Field(default_factory=list)
    skipped_gate_ids: list[str] = Field(default_factory=list)
    redaction: dict[str, Any] = Field(default_factory=lambda: {"safe_by_default": True})

    @field_validator("redaction")
    @classmethod
    def validate_redaction(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="evidence index redaction")


class CapabilityPackResolution(ContractModel):
    """Redaction-safe projection of selected pack/scenario/gate metadata."""

    pack_id: str | None = None
    pack_version: str | None = None
    adapter_id: str | None = None
    scenario_ids: list[str] = Field(default_factory=list)
    selected_gate_ids: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    required_skills: list[str] = Field(default_factory=list)
    context_requirements: list[str] = Field(default_factory=list)
    provider_route_requirements: dict[str, Any] = Field(default_factory=dict)
    policy_profile_defaults: dict[str, Any] = Field(default_factory=dict)
    supervision_expectations: dict[str, Any] = Field(default_factory=dict)
    gate_statuses: dict[str, str] = Field(default_factory=dict)
    skipped_gate_ids: list[str] = Field(default_factory=list)
    skipped_gate_reasons: dict[str, str] = Field(default_factory=dict)
    deterministic_commands: list[str] = Field(default_factory=list)
    optional_live_commands: list[str] = Field(default_factory=list)
    evidence_index: EvidenceArtifactIndex | None = None
    rollout_mode: str = "inert"
    compatibility: dict[str, Any] = Field(default_factory=dict)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "provider_route_requirements",
        "policy_profile_defaults",
        "supervision_expectations",
        "gate_statuses",
        "skipped_gate_reasons",
        "compatibility",
        "redacted_metadata",
    )
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="pack resolution metadata")


__all__ = [
    "CapabilityPackResolution",
    "EvidenceArtifactIndex",
    "EvidenceArtifactRef",
    "HarnessAdapterManifest",
    "HarnessCapabilityPack",
    "HarnessReleaseGate",
    "HarnessScenarioSpec",
]
