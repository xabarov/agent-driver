"""Continuous validation contracts for capability-pack release gates."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.contracts.validation import ensure_json_serializable

_CANDIDATE_STATUSES = frozenset({"passed", "failed", "blocked", "no_claim", "stale"})
_ADOPTION_STATUSES = frozenset(
    {
        "disabled",
        "metadata_only",
        "observe",
        "warn",
        "enforce",
        "live_validated",
        "rollback_required",
    }
)
_VALIDATION_ARTIFACT_TYPES = frozenset(
    {
        "manifest",
        "evidence_index",
        "validation_gates",
        "capability_pack_resolution",
        "capability_pack_run",
        "capability_pack_dry_run",
        "command_output",
        "support_bundle",
        "trace_summary",
        "phoenix_run_ids",
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
        "skill_inventory_snapshot",
        "skill_lockfile",
        "skill_reload_diff",
        "skill_selection_evidence",
        "skill_lifecycle_compatibility_report",
        "skip_justification",
        "validation_run_json",
        "validation_report_markdown",
        "other",
    }
)
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    return any(marker in lower for marker in _SECRET_KEY_MARKERS)


def _looks_like_env_name(value: str) -> bool:
    return bool(value) and value.upper() == value and " " not in value


def _assert_no_secret_fields(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if _is_sensitive_key(key_text):
                if not (isinstance(item, str) and _looks_like_env_name(item)):
                    raise ValueError(
                        "continuous validation metadata may name secret env vars "
                        f"but must not contain secret values: {next_path}"
                    )
            _assert_no_secret_fields(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_no_secret_fields(item, path=f"{path}[{index}]")


class ValidationArtifactRef(ContractModel):
    """Checksum-aware validation artifact reference."""

    artifact_id: str
    artifact_type: str = "other"
    path: str | None = None
    uri: str | None = None
    sha256: str | None = None
    size_bytes: int | None = None
    gate_id: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("artifact_type")
    @classmethod
    def validate_artifact_type(cls, value: str) -> str:
        if value not in _VALIDATION_ARTIFACT_TYPES:
            raise ValueError(f"unsupported validation artifact type: {value}")
        return value

    @field_validator("size_bytes")
    @classmethod
    def validate_size(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("size_bytes must be >= 0")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(
            value, field_name="validation artifact metadata"
        )


class ValidationRunRecord(ContractModel):
    """One offline or live validation attempt over evidence indexes."""

    run_id: str
    baseline_ids: list[str] = Field(default_factory=list)
    pack_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    product_adapter_ids: list[str] = Field(default_factory=list)
    git_refs: dict[str, str] = Field(default_factory=dict)
    command_refs: list[str] = Field(default_factory=list)
    artifact_index_refs: list[ValidationArtifactRef] = Field(default_factory=list)
    gate_results: list[ValidationGateResult] = Field(default_factory=list)
    started_at: str | None = None
    ended_at: str | None = None
    cost_summary: dict[str, Any] = Field(default_factory=dict)
    latency_summary: dict[str, Any] = Field(default_factory=dict)
    skip_reasons: dict[str, str] = Field(default_factory=dict)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "git_refs",
        "cost_summary",
        "latency_summary",
        "skip_reasons",
        "redacted_metadata",
    )
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="validation run metadata")

    @model_validator(mode="after")
    def validate_artifact_refs_and_secrets(self) -> "ValidationRunRecord":
        dumped = self.model_dump(mode="json")
        _assert_no_secret_fields(dumped)
        artifacts_by_gate = {
            ref.gate_id for ref in self.artifact_index_refs if ref.gate_id is not None
        }
        for gate in self.gate_results:
            if gate.status == "passed" and not gate.evidence_path:
                if gate.gate_id not in artifacts_by_gate:
                    raise ValueError(
                        "passed validation gates must include an evidence_path or "
                        f"artifact ref: {gate.gate_id}"
                    )
        return self


class HarnessBaseline(ContractModel):
    """Named expected state for a pack/scenario/product validation target."""

    baseline_id: str
    pack_id: str
    product_adapter_id: str
    scenario_ids: list[str] = Field(default_factory=list)
    expected_gate_statuses: dict[str, str] = Field(default_factory=dict)
    required_artifact_ids: list[str] = Field(default_factory=list)
    required_evidence: list[str] = Field(default_factory=list)
    gate_freshness_windows_hours: dict[str, int] = Field(default_factory=dict)
    quality_bounds: dict[str, Any] = Field(default_factory=dict)
    cost_latency_bounds: dict[str, Any] = Field(default_factory=dict)
    trace_violation_thresholds: dict[str, Any] = Field(default_factory=dict)
    owner: str = "agent-driver-harness"
    review_after: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("expected_gate_statuses")
    @classmethod
    def validate_expected_statuses(cls, value: dict[str, str]) -> dict[str, str]:
        for gate_id, status in value.items():
            ValidationGateResult(gate_id=gate_id, status=status)
        return value

    @field_validator("gate_freshness_windows_hours")
    @classmethod
    def validate_freshness(cls, value: dict[str, int]) -> dict[str, int]:
        for gate_id, hours in value.items():
            if hours <= 0:
                raise ValueError(f"freshness window for {gate_id} must be > 0")
        return value

    @field_validator(
        "quality_bounds",
        "cost_latency_bounds",
        "trace_violation_thresholds",
        "redacted_metadata",
    )
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="baseline metadata")

    @model_validator(mode="after")
    def validate_no_secret_values(self) -> "HarnessBaseline":
        _assert_no_secret_fields(self.model_dump(mode="json"))
        return self


class RegressionSummary(ContractModel):
    """Comparison between candidate validation evidence and seeded baselines."""

    summary_id: str
    validation_run_id: str
    baseline_ids: list[str] = Field(default_factory=list)
    new_failures: list[str] = Field(default_factory=list)
    fixed_failures: list[str] = Field(default_factory=list)
    stale_gates: list[str] = Field(default_factory=list)
    missing_artifacts: list[str] = Field(default_factory=list)
    corrupt_artifacts: list[str] = Field(default_factory=list)
    skipped_required_gates: list[str] = Field(default_factory=list)
    no_claim_gates: list[str] = Field(default_factory=list)
    cost_latency_deltas: dict[str, Any] = Field(default_factory=dict)
    benchmark_deltas: dict[str, Any] = Field(default_factory=dict)
    trace_violation_deltas: dict[str, Any] = Field(default_factory=dict)
    candidate_status: str = "no_claim"
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("candidate_status")
    @classmethod
    def validate_candidate_status(cls, value: str) -> str:
        if value not in _CANDIDATE_STATUSES:
            raise ValueError(f"unsupported candidate status: {value}")
        return value

    @field_validator(
        "cost_latency_deltas",
        "benchmark_deltas",
        "trace_violation_deltas",
        "redacted_metadata",
    )
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="regression metadata")


class ReleaseGatePolicy(ContractModel):
    """Blast-radius policy that decides which gates are required."""

    policy_id: str
    change_types: list[str] = Field(default_factory=list)
    required_gate_ids: list[str] = Field(default_factory=list)
    optional_gate_ids: list[str] = Field(default_factory=list)
    live_required_gate_ids: list[str] = Field(default_factory=list)
    ui_required_gate_ids: list[str] = Field(default_factory=list)
    benchmark_required_gate_ids: list[str] = Field(default_factory=list)
    stale_allowed_gate_ids: list[str] = Field(default_factory=list)
    blocked_gate_ids: list[str] = Field(default_factory=list)
    max_cost_usd: float | None = None
    timeout_seconds: int | None = None
    retry_budget: int = 0
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_cost_usd")
    @classmethod
    def validate_cost(cls, value: float | None) -> float | None:
        if value is not None and value < 0:
            raise ValueError("max_cost_usd must be >= 0")
        return value

    @field_validator("timeout_seconds", "retry_budget")
    @classmethod
    def validate_budget(cls, value: int | None) -> int | None:
        if value is not None and value < 0:
            raise ValueError("budget values must be >= 0")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(
            value, field_name="release gate policy metadata"
        )


class FlakeRecord(ContractModel):
    """Visible quarantine record for a flaky scenario/gate."""

    flake_id: str
    scenario_id: str
    gate_id: str
    owner: str
    first_seen: str
    last_seen: str
    quarantined: bool = True
    quarantine_expires: str
    repro_command: str
    evidence_links: list[str] = Field(default_factory=list)
    promotion_notes: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="flake record metadata")


class HostAdoptionState(ContractModel):
    """Product-owned rollout status for pack/scenario validation metadata."""

    product_adapter_id: str
    pack_id: str
    status: str = "metadata_only"
    scenario_ids: list[str] = Field(default_factory=list)
    metadata_paths: list[str] = Field(default_factory=list)
    required_gate_ids: list[str] = Field(default_factory=list)
    rollback_notes: list[str] = Field(default_factory=list)
    behavior_change_enabled: bool = False
    owner: str
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        if value not in _ADOPTION_STATUSES:
            raise ValueError(f"unsupported host adoption status: {value}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return ensure_json_serializable(value, field_name="host adoption metadata")

    @model_validator(mode="after")
    def validate_no_secret_values(self) -> "HostAdoptionState":
        _assert_no_secret_fields(self.model_dump(mode="json"))
        return self


class ValidationDashboardSummary(ContractModel):
    """Compact CI/human report over validation and regression evidence."""

    summary_id: str
    validation_run_id: str
    candidate_status: str
    product_rows: list[dict[str, Any]] = Field(default_factory=list)
    shared_rows: list[dict[str, Any]] = Field(default_factory=list)
    top_failures: list[str] = Field(default_factory=list)
    artifact_paths: list[str] = Field(default_factory=list)
    required_followups: list[str] = Field(default_factory=list)
    skipped_live_gates: list[str] = Field(default_factory=list)
    no_claim_gates: list[str] = Field(default_factory=list)
    redaction: dict[str, Any] = Field(default_factory=lambda: {"safe_by_default": True})

    @field_validator("candidate_status")
    @classmethod
    def validate_candidate_status(cls, value: str) -> str:
        if value not in _CANDIDATE_STATUSES:
            raise ValueError(f"unsupported dashboard status: {value}")
        return value

    @field_validator("product_rows", "shared_rows", "redaction")
    @classmethod
    def validate_json_fields(cls, value: Any) -> Any:
        return ensure_json_serializable(value, field_name="dashboard summary metadata")


__all__ = [
    "FlakeRecord",
    "HarnessBaseline",
    "HostAdoptionState",
    "RegressionSummary",
    "ReleaseGatePolicy",
    "ValidationArtifactRef",
    "ValidationDashboardSummary",
    "ValidationRunRecord",
]
