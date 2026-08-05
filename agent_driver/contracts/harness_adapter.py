"""Harness adapter protocol contracts for host compatibility surfaces."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    assert_no_secret_fields,
    ensure_json_serializable,
)

ADAPTER_FEATURE_STATUSES = frozenset(
    {"supported", "unsupported", "skipped", "stale", "no_claim", "failed", "passed"}
)
ADAPTER_DURABILITY_LEVELS = frozenset(
    {"process_local", "sqlite", "external_db", "managed_host", "unknown"}
)
ADAPTER_EVENT_SOURCES = frozenset({"live", "replay", "synthetic"})
ADAPTER_CONTROL_KINDS = frozenset(
    {
        "abort",
        "pause",
        "resume",
        "steering_update",
        "approval_resolution",
        "mode_change",
    }
)
_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")


def _validate_json_map(value: dict[str, Any], *, field_name: str) -> dict[str, Any]:
    safe = ensure_json_serializable(value, field_name=field_name)
    assert_no_secret_fields(
        safe, subject="harness adapter metadata", markers=_SECRET_KEY_MARKERS
    )
    return safe


class HarnessArtifactRef(ContractModel):
    """Adapter-safe pointer to a produced artifact or evidence row."""

    artifact_id: str
    artifact_type: str
    path: str | None = None
    uri: str | None = None
    sha256: str | None = None
    gate_id: str | None = None
    scenario_id: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness artifact metadata")


class HarnessSupportBundleRef(ContractModel):
    """Compact reference to support, trace, validation or screenshot evidence."""

    bundle_id: str
    bundle_type: str = "support_bundle"
    path: str | None = None
    uri: str | None = None
    sha256: str | None = None
    gate_id: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness support bundle metadata")


class HarnessApprovalRequest(ContractModel):
    """Adapter-safe approval request surfaced to host clients."""

    request_id: str
    run_id: str
    attempt_id: str
    tool_name: str | None = None
    tool_call_id: str | None = None
    side_effect_class: str | None = None
    arguments_summary: str | None = None
    policy_verdict: str | None = None
    expires_at: str | None = None
    response_options: list[str] = Field(default_factory=list)
    redacted_details: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_details")
    @classmethod
    def validate_details(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness approval details")


class HarnessAdapterEvent(ContractModel):
    """Canonical stream event row for harness protocol adapters."""

    event_id: str
    session_id: str | None = None
    run_id: str
    attempt_id: str
    cursor: str
    seq: int
    kind: str
    category: str
    state: str
    source: str = "replay"
    display: dict[str, Any] = Field(default_factory=dict)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    support_bundle_refs: list[HarnessSupportBundleRef] = Field(default_factory=list)
    approval_request: HarnessApprovalRequest | None = None
    created_at: str | None = None

    @field_validator("source")
    @classmethod
    def validate_source(cls, value: str) -> str:
        if value not in ADAPTER_EVENT_SOURCES:
            raise ValueError(f"unsupported harness adapter event source: {value}")
        return value

    @field_validator("display", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness adapter event metadata")

    @model_validator(mode="after")
    def validate_cursor(self) -> "HarnessAdapterEvent":
        expected = f"{self.run_id}:{self.seq}"
        if self.cursor != expected:
            raise ValueError(f"adapter event cursor must be stable run:seq: {expected}")
        return self


class HarnessAdapterSession(ContractModel):
    """Stable session descriptor exposed by harness adapters."""

    session_id: str
    thread_id: str | None = None
    adapter_id: str
    cwd: str | None = None
    provider_route_summary: dict[str, Any] = Field(default_factory=dict)
    mode: str = "default"
    lifecycle_state: str = "unknown"
    durability_level: str = "unknown"
    created_at: str | None = None
    updated_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)
    support_bundle_refs: list[HarnessSupportBundleRef] = Field(default_factory=list)

    @field_validator("durability_level")
    @classmethod
    def validate_durability(cls, value: str) -> str:
        if value not in ADAPTER_DURABILITY_LEVELS:
            raise ValueError(f"unsupported harness adapter durability level: {value}")
        return value

    @field_validator("provider_route_summary", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness adapter session metadata")


class HarnessAdapterRun(ContractModel):
    """Stable run descriptor exposed by harness adapters."""

    run_id: str
    attempt_id: str
    session_id: str | None = None
    current_cursor: str | None = None
    lifecycle_state: str = "unknown"
    durability_level: str = "unknown"
    supervisor_summary: dict[str, Any] = Field(default_factory=dict)
    capability_pack_ids: list[str] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    support_bundle_refs: list[HarnessSupportBundleRef] = Field(default_factory=list)
    compatibility_flags: dict[str, str] = Field(default_factory=dict)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("durability_level")
    @classmethod
    def validate_durability(cls, value: str) -> str:
        if value not in ADAPTER_DURABILITY_LEVELS:
            raise ValueError(f"unsupported harness adapter durability level: {value}")
        return value

    @field_validator("supervisor_summary", "compatibility_flags", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness adapter run metadata")


class HarnessAdapterControl(ContractModel):
    """Host-to-runtime control request shape for adapter protocols."""

    control_id: str
    control_kind: str
    session_id: str | None = None
    run_id: str | None = None
    cursor: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    requested_by: str | None = None
    created_at: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("control_kind")
    @classmethod
    def validate_control_kind(cls, value: str) -> str:
        if value not in ADAPTER_CONTROL_KINDS:
            raise ValueError(f"unsupported harness adapter control kind: {value}")
        return value

    @field_validator("payload", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness adapter control metadata")


class HarnessAdapterCapability(ContractModel):
    """Product/protocol feature manifest for harness adapter compatibility."""

    adapter_id: str
    product_family: str
    protocol: str = "harness_adapter"
    durability_level: str = "unknown"
    features: dict[str, str] = Field(default_factory=dict)
    scenario_ids: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("durability_level")
    @classmethod
    def validate_durability(cls, value: str) -> str:
        if value not in ADAPTER_DURABILITY_LEVELS:
            raise ValueError(f"unsupported harness adapter durability level: {value}")
        return value

    @field_validator("features")
    @classmethod
    def validate_features(cls, value: dict[str, str]) -> dict[str, str]:
        for feature, status in value.items():
            if status not in ADAPTER_FEATURE_STATUSES:
                raise ValueError(f"unsupported status for {feature}: {status}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(
            value, field_name="harness adapter capability metadata"
        )


class HarnessAdapterCompatibilityReport(ContractModel):
    """Deterministic report proving adapter feature compatibility truthfully."""

    report_id: str
    adapter_id: str
    product_family: str
    protocol: str = "harness_adapter"
    generated_at: str | None = None
    no_live: bool = True
    capability: HarnessAdapterCapability
    feature_statuses: dict[str, str] = Field(default_factory=dict)
    session: HarnessAdapterSession | None = None
    run: HarnessAdapterRun | None = None
    event_count: int = 0
    artifact_refs: list[HarnessArtifactRef] = Field(default_factory=list)
    support_bundle_refs: list[HarnessSupportBundleRef] = Field(default_factory=list)
    scenario_ids: list[str] = Field(default_factory=list)
    validation_gate_statuses: dict[str, str] = Field(default_factory=dict)
    skipped_reasons: dict[str, str] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(default_factory=lambda: {"safe_by_default": True})
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("feature_statuses", "validation_gate_statuses")
    @classmethod
    def validate_status_maps(cls, value: dict[str, str]) -> dict[str, str]:
        for feature, status in value.items():
            if status not in ADAPTER_FEATURE_STATUSES:
                raise ValueError(f"unsupported status for {feature}: {status}")
        return value

    @field_validator("skipped_reasons", "redaction", "redacted_metadata")
    @classmethod
    def validate_json_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value, field_name="harness adapter report metadata")


__all__ = [
    "ADAPTER_DURABILITY_LEVELS",
    "ADAPTER_FEATURE_STATUSES",
    "HarnessAdapterCapability",
    "HarnessAdapterCompatibilityReport",
    "HarnessAdapterControl",
    "HarnessAdapterEvent",
    "HarnessAdapterRun",
    "HarnessAdapterSession",
    "HarnessApprovalRequest",
    "HarnessArtifactRef",
    "HarnessSupportBundleRef",
]
