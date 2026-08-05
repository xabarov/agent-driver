"""Redaction-safe contracts for governed MCP server/tool/resource evidence.

Epic 014 (MCP governance and evidence plane). These contracts sit on top of the
existing MCP transport (``agent_driver/mcp_server``) and client tools
(``agent_driver/tools/builtin/mcp.py``). They add a governance/provenance plane:
which servers/tools/resources were allowed, why a call was approved/asked/blocked,
and redaction-safe provenance rows — never raw resource bodies or credentials.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
    is_sensitive_key,
    looks_like_env_name,
)

McpGovernanceStatus = Literal[
    "registered",
    "allowed",
    "asked",
    "blocked",
    "filtered",
    "out_of_roots",
    "oversized",
    "unavailable",
    "stale",
    "skipped",
    "no_claim",
    "failed",
]
McpTrustClass = Literal[
    "curated",
    "host_bundle",
    "workspace",
    "user",
    "external",
    "unknown",
]
McpTransport = Literal["stdio", "http", "sse", "in_process", "unknown"]
McpCapability = Literal[
    "tools",
    "resources",
    "prompts",
    "sampling",
    "elicitation",
    "completion",
    "logging",
    "roots",
]
McpRefKind = Literal["tool", "resource", "prompt"]
McpApprovalAction = Literal["allow", "ask", "block"]
McpRedactionStatus = Literal["redacted", "summary", "none"]
McpReadStatus = Literal[
    "not_read",
    "read",
    "missing",
    "truncated",
    "blocked",
    "failed",
]

_DECISION_STATUSES = frozenset(
    {
        "allowed",
        "asked",
        "blocked",
        "filtered",
        "out_of_roots",
        "oversized",
        "no_claim",
        "failed",
    }
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth", "credential")
# Keys that legitimately contain a marker substring but name a mechanism/mode,
# never a secret value (e.g. auth_mode = "none"/"token"/"oauth").
_SAFE_METADATA_KEYS = frozenset({"auth_mode"})
_RAW_CONTENT_MARKERS = (
    "body",
    "content",
    "raw",
    "payload",
    "resource_body",
    "resource_text",
    "prompt_body",
)


def _assert_redaction_safe(value: Any, *, path: str = "") -> None:
    """Reject secret-shaped values and raw MCP resource/prompt content fields."""
    if isinstance(value, dict):
        for key, item in value.items():
            key_text = str(key)
            lower = key_text.lower()
            next_path = f"{path}.{key_text}" if path else key_text
            if is_sensitive_key(
                key_text, markers=_SECRET_KEY_MARKERS, safe_keys=_SAFE_METADATA_KEYS
            ):
                if not (isinstance(item, str) and looks_like_env_name(item)):
                    raise ValueError(
                        "mcp governance metadata may name secret env vars but must "
                        f"not contain secret values: {next_path}"
                    )
            if any(
                marker == lower or lower.endswith(f"_{marker}")
                for marker in _RAW_CONTENT_MARKERS
            ):
                raise ValueError(
                    "mcp governance reports must not include raw resource/prompt "
                    f"bodies: {next_path}"
                )
            _assert_redaction_safe(item, path=next_path)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_redaction_safe(item, path=f"{path}[{index}]")


def _validate_json_safe_metadata(
    value: dict[str, Any], *, field_name: str
) -> dict[str, Any]:
    value = ensure_json_serializable(value, field_name=field_name)
    _assert_redaction_safe(value)
    return value


class McpToolResourceRef(ContractModel):
    """Redaction-safe reference to one MCP tool/resource/prompt."""

    server_id: str
    name: str
    kind: McpRefKind = "tool"
    capability: McpCapability = "tools"
    side_effect_class: str = "read_only"
    allow_state: McpGovernanceStatus = "registered"
    uri: str | None = None
    checksum: str | None = None
    size_bytes: int = 0
    read_status: McpReadStatus = "not_read"
    redaction_status: McpRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("size_bytes")
    @classmethod
    def validate_size_bytes(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="size_bytes") or 0

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp ref metadata")


class McpServerDescriptor(ContractModel):
    """One MCP server registry entry without embedding credentials."""

    server_id: str
    name: str
    transport: McpTransport = "stdio"
    endpoint_ref: str | None = None
    auth_mode: str = "none"
    trust_class: McpTrustClass = "unknown"
    allowed_roots: list[str] = Field(default_factory=list)
    capabilities: list[McpCapability] = Field(default_factory=list)
    health_check_supported: bool = False
    status: McpGovernanceStatus = "registered"
    digest: str
    redaction_status: McpRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("auth_mode")
    @classmethod
    def validate_auth_mode(cls, value: str) -> str:
        # auth_mode names a mechanism (none/token/oauth/env), never a secret.
        # A long opaque string is treated as an accidental credential.
        if len(value) > 32 and not looks_like_env_name(value):
            raise ValueError("auth_mode must name a mechanism, not a credential")
        return value

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: list[McpCapability]) -> list[McpCapability]:
        return list(dict.fromkeys(value))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp server metadata")


class McpRegistrySnapshot(ContractModel):
    """Deterministic scan of configured MCP servers and their tools/resources."""

    snapshot_id: str
    server_refs: list[McpServerDescriptor] = Field(default_factory=list)
    allowed_roots: list[str] = Field(default_factory=list)
    discovery_limits: dict[str, Any] = Field(default_factory=dict)
    returned_count: int = 0
    truncated_count: int = 0
    tool_resource_refs: list[McpToolResourceRef] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    created_at: str | None = None
    digest: str
    redaction_status: McpRedactionStatus = "redacted"

    @field_validator("returned_count", "truncated_count")
    @classmethod
    def validate_counts(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="registry count") or 0

    @field_validator("discovery_limits")
    @classmethod
    def validate_discovery_limits(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp discovery limits")

    @model_validator(mode="after")
    def validate_unique_server_ids(self) -> "McpRegistrySnapshot":
        _assert_unique_server_ids(self.server_refs)
        return self


class McpApprovalPolicy(ContractModel):
    """Per-server/tool approval + sandbox rules used deterministically."""

    policy_id: str
    server_id: str | None = None
    ref_name: str | None = None
    default_action: McpApprovalAction = "ask"
    roots_boundary: list[str] = Field(default_factory=list)
    allowed_side_effect_classes: list[str] = Field(default_factory=list)
    blocked_side_effect_classes: list[str] = Field(default_factory=list)
    sampling_allowed: bool = False
    elicitation_allowed: bool = False
    max_resource_bytes: int | None = None
    timeout_seconds: float | None = None
    sandbox_mode: str | None = None
    required_evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("max_resource_bytes")
    @classmethod
    def validate_max_resource_bytes(cls, value: int | None) -> int | None:
        return ensure_non_negative_int(value, field_name="max_resource_bytes")

    @field_validator("timeout_seconds")
    @classmethod
    def validate_timeout(cls, value: float | None) -> float | None:
        if value is not None and value <= 0:
            raise ValueError("timeout_seconds must be > 0")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp policy metadata")


class McpApprovalDecision(ContractModel):
    """Compact approval evidence without raw payloads or credentials."""

    decision_id: str
    policy_id: str | None = None
    server_id: str
    ref_name: str
    kind: McpRefKind = "tool"
    status: McpGovernanceStatus
    action: McpApprovalAction = "block"
    rationale: str = ""
    filter_reasons: list[str] = Field(default_factory=list)
    roots_used: list[str] = Field(default_factory=list)
    sampling_involved: bool = False
    elicitation_involved: bool = False
    redaction_status: McpRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("status")
    @classmethod
    def validate_decision_status(
        cls, value: McpGovernanceStatus
    ) -> McpGovernanceStatus:
        if value not in _DECISION_STATUSES:
            raise ValueError(f"unsupported mcp approval status: {value}")
        return value

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp decision metadata")


class McpCallProvenanceRow(ContractModel):
    """Expanded telemetry for an MCP tool call or resource read."""

    call_id: str
    server_id: str
    ref_name: str
    kind: McpRefKind = "tool"
    digest: str | None = None
    roots_used: list[str] = Field(default_factory=list)
    sampling_involved: bool = False
    elicitation_involved: bool = False
    read_status: McpReadStatus = "not_read"
    tool_call_id: str | None = None
    run_id: str | None = None
    session_id: str | None = None
    latency_ms: int | None = None
    artifact_refs: list[str] = Field(default_factory=list)
    source_refs: list[str] = Field(default_factory=list)
    provenance_refs: list[str] = Field(default_factory=list)
    redaction_status: McpRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("latency_ms")
    @classmethod
    def validate_latency(cls, value: int | None) -> int | None:
        return ensure_non_negative_int(value, field_name="latency_ms")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp provenance metadata")


class McpGovernanceUsageSummary(ContractModel):
    """Deterministic MCP governance counters and optional outcome linkage."""

    registered: int = 0
    allowed: int = 0
    asked: int = 0
    blocked: int = 0
    filtered: int = 0
    out_of_roots: int = 0
    oversized: int = 0
    failed: int = 0
    stale: int = 0
    outcome_links: dict[str, Any] = Field(default_factory=dict)
    redaction_status: McpRedactionStatus = "redacted"

    @field_validator(
        "registered",
        "allowed",
        "asked",
        "blocked",
        "filtered",
        "out_of_roots",
        "oversized",
        "failed",
        "stale",
    )
    @classmethod
    def validate_counter(cls, value: int) -> int:
        return ensure_non_negative_int(value, field_name="mcp usage counter") or 0

    @field_validator("outcome_links")
    @classmethod
    def validate_outcome_links(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp usage outcome links")


class McpGovernanceCompatibilityReport(ContractModel):
    """Host/product compatibility projection for governed MCP servers."""

    report_id: str
    product_family: str
    host_profile: str
    registry_snapshot_id: str | None = None
    roots_allowed: list[str] = Field(default_factory=list)
    servers_registered: list[str] = Field(default_factory=list)
    policies_applied: list[McpApprovalPolicy] = Field(default_factory=list)
    approvals_recorded: list[McpApprovalDecision] = Field(default_factory=list)
    provenance_rows: list[McpCallProvenanceRow] = Field(default_factory=list)
    usage_summary: McpGovernanceUsageSummary = Field(
        default_factory=McpGovernanceUsageSummary
    )
    no_claims: list[str] = Field(default_factory=list)
    support_bundle_projection: list[dict[str, Any]] = Field(default_factory=list)
    evidence_refs: list[str] = Field(default_factory=list)
    redaction_status: McpRedactionStatus = "redacted"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("support_bundle_projection")
    @classmethod
    def validate_report_rows(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [
            _validate_json_safe_metadata(item, field_name="mcp report row")
            for item in value
        ]

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_json_safe_metadata(value, field_name="mcp report metadata")


def _assert_unique_server_ids(records: list[McpServerDescriptor]) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()
    for record in records:
        if record.server_id in seen:
            duplicates.add(record.server_id)
        seen.add(record.server_id)
    if duplicates:
        joined = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate mcp server ids: {joined}")


__all__ = [
    "McpApprovalAction",
    "McpApprovalDecision",
    "McpApprovalPolicy",
    "McpCallProvenanceRow",
    "McpCapability",
    "McpGovernanceCompatibilityReport",
    "McpGovernanceStatus",
    "McpGovernanceUsageSummary",
    "McpReadStatus",
    "McpRedactionStatus",
    "McpRefKind",
    "McpRegistrySnapshot",
    "McpServerDescriptor",
    "McpToolResourceRef",
    "McpTransport",
    "McpTrustClass",
]
