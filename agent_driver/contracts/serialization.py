"""Executor boundary serialization policy contracts."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.enums import SerializationMode
from agent_driver.contracts.validation import ensure_json_serializable


class ExecutorSerializationPolicy(ContractModel):
    """Serialization safety policy for sandbox/worker boundaries."""

    mode: SerializationMode = SerializationMode.JSON_SAFE
    allow_unsafe_payloads: bool = False
    schema_version: str = "v1"
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure metadata stays JSON-compatible."""
        return ensure_json_serializable(
            value, field_name="serialization policy metadata"
        )
