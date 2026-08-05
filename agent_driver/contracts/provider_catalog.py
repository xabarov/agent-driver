"""Provider plugin, catalog, routing, and compatibility contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_float,
    ensure_positive_int,
    is_sensitive_key,
    looks_like_env_name,
)

PROVIDER_CATALOG_STATUSES = frozenset(
    {
        "supported",
        "unsupported",
        "degraded",
        "blocked",
        "skipped",
        "stale",
        "no_claim",
        "failed",
        "cache_hit",
        "cache_stale",
    }
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")
_RAW_RESPONSE_MARKERS = ("raw_response", "raw_provider_response", "response_body")
_SAFE_TOKEN_METRIC_KEYS = {
    "max_tokens",
    "max_output_tokens",
    "max_completion_tokens",
    "input_tokens",
    "output_tokens",
    "total_tokens",
    "max_token_field",
}


def _is_raw_response_key(key: str) -> bool:
    lower = key.lower()
    return any(marker in lower for marker in _RAW_RESPONSE_MARKERS)


def _assert_redaction_safe(value: Any, *, path: str = "") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            next_path = f"{path}.{key_text}" if path else key_text
            if _is_raw_response_key(key_text) and not isinstance(item, bool):
                raise ValueError(
                    f"provider catalog metadata must not contain raw provider "
                    f"responses: {next_path}"
                )
            if is_sensitive_key(
                key_text,
                markers=_SECRET_KEY_MARKERS,
                safe_keys=_SAFE_TOKEN_METRIC_KEYS,
            ):
                if not (
                    isinstance(item, bool)
                    or (isinstance(item, str) and looks_like_env_name(item))
                ):
                    raise ValueError(
                        f"provider catalog metadata may name secret env vars but "
                        f"must not contain secret values: {next_path}"
                    )
            _assert_redaction_safe(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_redaction_safe(item, path=f"{path}[{index}]")


def _validate_status(value: str) -> str:
    if value not in PROVIDER_CATALOG_STATUSES:
        raise ValueError(f"unsupported provider catalog status: {value}")
    return value


def _validate_json_map(value: dict[str, Any]) -> dict[str, Any]:
    return ensure_json_serializable(value, field_name="provider catalog metadata")


class ProviderPluginManifest(ContractModel):
    """Authorable plugin metadata above provider construction descriptors."""

    plugin_id: str
    version: str
    provider_ids: list[str] = Field(default_factory=list)
    aliases: list[str] = Field(default_factory=list)
    transports: list[str] = Field(default_factory=list)
    owner: str | None = None
    source: str = "builtin"
    model_family_rules: dict[str, Any] = Field(default_factory=dict)
    env_alias_names: list[str] = Field(default_factory=list)
    catalog_strategy: str = "deterministic_fixture"
    compatibility_notes: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("model_family_rules", "redacted_metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderPluginManifest":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderRoutePlugin(ContractModel):
    """Registered route hook declaration for one provider/model family."""

    plugin_id: str
    provider_id: str
    model_family: str
    transport: str
    source: str = "builtin"
    route_profile_id: str | None = None
    hook_names: list[str] = Field(default_factory=list)
    sanitizer_rule_ids: list[str] = Field(default_factory=list)
    compatibility_notes: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderRoutePlugin":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderCatalogModel(ContractModel):
    """One redaction-safe model row in a provider catalog cache."""

    model_id: str
    aliases: list[str] = Field(default_factory=list)
    context_window: int | None = None
    max_output_tokens: int | None = None
    modalities: list[str] = Field(default_factory=list)
    capability_statuses: dict[str, str] = Field(default_factory=dict)
    cost_hints: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)

    @field_validator("context_window", "max_output_tokens")
    @classmethod
    def validate_positive_ints(cls, value: int | None) -> int | None:
        return ensure_positive_int(value, field_name="catalog model integer")

    @field_validator("capability_statuses")
    @classmethod
    def validate_capability_statuses(cls, value: dict[str, str]) -> dict[str, str]:
        for status in value.values():
            _validate_status(status)
        return value

    @field_validator("cost_hints")
    @classmethod
    def validate_cost_hints(cls, value: dict[str, float]) -> dict[str, float]:
        for cost in value.values():
            ensure_non_negative_float(cost, field_name="catalog model cost hint")
        return value


class ProviderModelCatalog(ContractModel):
    """Cached model catalog with freshness, checksum, source, and redaction state."""

    catalog_id: str
    provider_id: str
    version: str
    source: str
    fetched_at: str | None = None
    freshness_status: str = "no_claim"
    checksum: str
    models: list[ProviderCatalogModel] = Field(default_factory=list)
    redaction: dict[str, Any] = Field(
        default_factory=lambda: {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_provider_response": False,
        }
    )
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("freshness_status")
    @classmethod
    def validate_freshness_status(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator("redaction", "redacted_metadata")
    @classmethod
    def validate_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderModelCatalog":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderCatalogFetchPlan(ContractModel):
    """Deterministic/live plan for obtaining a provider model catalog."""

    provider_id: str
    status: str
    live_allowed: bool = False
    cache_path: str | None = None
    source: str | None = None
    reason: str | None = None
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        allowed = PROVIDER_CATALOG_STATUSES | {
            "not_needed",
            "cache_stale",
            "fetch_skipped",
            "fetch_allowed",
            "fetch_failed",
        }
        if value not in allowed:
            raise ValueError(f"unsupported catalog fetch status: {value}")
        return value

    @field_validator("redacted_metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderCatalogFetchPlan":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderRouteCapabilityMatrix(ContractModel):
    """Normalized capability row for one provider/model route family."""

    matrix_id: str
    provider_id: str
    model_family: str
    transport: str
    source: str
    freshness_status: str = "no_claim"
    tool_calls: str = "no_claim"
    forced_tool_choice: str = "no_claim"
    strict_json_schema: str = "no_claim"
    reasoning: str = "no_claim"
    reasoning_echo: str = "no_claim"
    streaming: str = "no_claim"
    native_web: str = "no_claim"
    vision: str = "no_claim"
    parallel_tools: str = "no_claim"
    max_token_field: str = "max_tokens"
    request_id: str = "no_claim"
    rate_cost_hints: dict[str, Any] = Field(default_factory=dict)
    known_downgrades: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator(
        "freshness_status",
        "tool_calls",
        "forced_tool_choice",
        "strict_json_schema",
        "reasoning",
        "reasoning_echo",
        "streaming",
        "native_web",
        "vision",
        "parallel_tools",
        "request_id",
    )
    @classmethod
    def validate_status_fields(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator("rate_cost_hints", "redacted_metadata")
    @classmethod
    def validate_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderRouteCapabilityMatrix":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderRequestSanitizerFixture(ContractModel):
    """Deterministic proof that a request is accepted, reshaped, or no-claimed."""

    fixture_id: str
    provider_id: str
    model_id: str
    feature: str
    verdict: str
    reason: str
    status: str
    source: str
    checksum: str
    request_shape: dict[str, Any] = Field(default_factory=dict)
    downgrades: list[str] = Field(default_factory=list)
    blocked_reasons: list[str] = Field(default_factory=list)
    redaction: dict[str, Any] = Field(
        default_factory=lambda: {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_provider_response": False,
        }
    )

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator("request_shape", "redaction")
    @classmethod
    def validate_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderRequestSanitizerFixture":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderRoutingPlan(ContractModel):
    """Evidence-bearing plan for route selection and request reshaping."""

    plan_id: str
    status: str
    selected_provider_id: str | None = None
    selected_model_id: str | None = None
    requested_capabilities: list[str] = Field(default_factory=list)
    alternatives: list[dict[str, Any]] = Field(default_factory=list)
    constraints: list[str] = Field(default_factory=list)
    downgrades: list[str] = Field(default_factory=list)
    fallback_eligible: bool = False
    health_status: dict[str, Any] = Field(default_factory=dict)
    cost_latency_hints: dict[str, Any] = Field(default_factory=dict)
    live_gate_required: bool = False
    required_live_gates: list[str] = Field(default_factory=list)
    redacted_metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_status(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator(
        "alternatives",
        "health_status",
        "cost_latency_hints",
        "redacted_metadata",
    )
    @classmethod
    def validate_maps(cls, value: Any) -> Any:
        return ensure_json_serializable(
            value, field_name="provider routing plan metadata"
        )

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderRoutingPlan":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderPreflightReport(ContractModel):
    """Generalized deterministic/live provider preflight report."""

    report_id: str
    provider_id: str
    model_id: str
    route_profile: dict[str, Any] = Field(default_factory=dict)
    capability_matrix: ProviderRouteCapabilityMatrix
    request_shape_plan: dict[str, Any] = Field(default_factory=dict)
    catalog_fetch_plan: ProviderCatalogFetchPlan
    catalog_freshness_status: str = "no_claim"
    live_result: dict[str, Any] = Field(default_factory=dict)
    phoenix_trace_ids: list[str] = Field(default_factory=list)
    validation_gate_statuses: dict[str, str] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(
        default_factory=lambda: {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_provider_response": False,
        }
    )

    @field_validator("catalog_freshness_status")
    @classmethod
    def validate_catalog_status(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator(
        "route_profile",
        "request_shape_plan",
        "live_result",
        "validation_gate_statuses",
        "redaction",
    )
    @classmethod
    def validate_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderPreflightReport":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


class ProviderCompatibilityReport(ContractModel):
    """Product-neutral provider compatibility report for validation audits."""

    report_id: str
    status: str
    deterministic_status: str
    catalog_status: str
    live_status: str = "no_claim"
    phoenix_status: str = "no_claim"
    benchmark_status: str = "no_claim"
    no_claim_reasons: list[str] = Field(default_factory=list)
    manifests: list[ProviderPluginManifest] = Field(default_factory=list)
    route_plugins: list[ProviderRoutePlugin] = Field(default_factory=list)
    catalogs: list[ProviderModelCatalog] = Field(default_factory=list)
    capability_matrix: list[ProviderRouteCapabilityMatrix] = Field(default_factory=list)
    sanitizer_fixtures: list[ProviderRequestSanitizerFixture] = Field(default_factory=list)
    routing_plans: list[ProviderRoutingPlan] = Field(default_factory=list)
    preflight_reports: list[ProviderPreflightReport] = Field(default_factory=list)
    evidence_index: dict[str, Any] = Field(default_factory=dict)
    redaction: dict[str, Any] = Field(
        default_factory=lambda: {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_provider_response": False,
        }
    )

    @field_validator(
        "status",
        "deterministic_status",
        "catalog_status",
        "live_status",
        "phoenix_status",
        "benchmark_status",
    )
    @classmethod
    def validate_status_fields(cls, value: str) -> str:
        return _validate_status(value)

    @field_validator("evidence_index", "redaction")
    @classmethod
    def validate_maps(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_map(value)

    @model_validator(mode="after")
    def validate_redaction(self) -> "ProviderCompatibilityReport":
        _assert_redaction_safe(self.model_dump(mode="json"))
        return self


__all__ = [
    "PROVIDER_CATALOG_STATUSES",
    "ProviderCatalogFetchPlan",
    "ProviderCatalogModel",
    "ProviderCompatibilityReport",
    "ProviderModelCatalog",
    "ProviderPluginManifest",
    "ProviderPreflightReport",
    "ProviderRequestSanitizerFixture",
    "ProviderRouteCapabilityMatrix",
    "ProviderRoutePlugin",
    "ProviderRoutingPlan",
]
