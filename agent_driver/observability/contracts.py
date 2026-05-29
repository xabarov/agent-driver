"""Phase-5 observability contracts for deterministic trace export."""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import Field, field_validator, model_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.validation import (
    ensure_json_serializable,
    ensure_non_negative_int,
)


class TraceSpan(ContractModel):
    """One normalized span entry derived from runtime events."""

    span_id: str
    event_id: str
    run_id: str
    attempt_id: str
    seq: int
    event_type: str
    node_id: str | None = None
    checkpoint_id: str | None = None
    created_at: str
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("seq")
    @classmethod
    def validate_seq(cls, value: int) -> int:
        """Require non-negative sequence for normalized spans."""
        return int(ensure_non_negative_int(value, field_name="seq"))

    @field_validator("payload", "metadata")
    @classmethod
    def validate_json_fields(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure payload/metadata stay JSON-compatible."""
        return ensure_json_serializable(value, field_name="trace span field")


class TraceExport(ContractModel):
    """Portable deterministic trace payload for local exporters/evals."""

    trace_id: str
    run_id: str
    attempt_id: str
    spans: list[TraceSpan] = Field(default_factory=list)
    tool_trace: list[dict[str, Any]] = Field(default_factory=list)
    usage: dict[str, Any] | None = None
    checkpoint: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("tool_trace")
    @classmethod
    def validate_tool_trace(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Ensure tool trace entries are JSON-compatible."""
        return [
            ensure_json_serializable(item, field_name="tool trace item")
            for item in value
        ]

    @field_validator("usage", "checkpoint")
    @classmethod
    def validate_optional_json(
        cls, value: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Validate optional JSON-compatible sections."""
        if value is None:
            return value
        return ensure_json_serializable(value, field_name="trace export section")

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="trace export metadata")

    @model_validator(mode="after")
    def validate_identity(self) -> "TraceExport":
        """Require stable run identity for exported spans."""
        for span in self.spans:
            if span.run_id != self.run_id:
                raise ValueError("trace span run_id mismatch")
            if span.attempt_id != self.attempt_id:
                raise ValueError("trace span attempt_id mismatch")
        return self


class TraceSinkResult(ContractModel):
    """Result metadata returned by trace exporter implementations."""

    sink: str
    trace_id: str
    span_count: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("span_count")
    @classmethod
    def validate_span_count(cls, value: int) -> int:
        """Require non-negative exported span count."""
        return int(ensure_non_negative_int(value, field_name="span_count"))

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="trace sink metadata")


class TraceExporter(Protocol):  # pylint: disable=too-few-public-methods
    """Protocol for writing deterministic trace exports to a sink."""

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Persist one trace payload and return sink metadata."""
        raise NotImplementedError
