"""Runtime state contracts for fake durable runner."""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.contracts.validation import ensure_json_serializable


class RuntimeState(ContractModel):
    """Canonical runtime state snapshot stored in checkpoints."""

    run_input: AgentRunInput
    latest_output: AgentRunOutput | None = None
    events: list[RuntimeEvent] = Field(default_factory=list)
    checkpoint: CheckpointRef | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("metadata")
    @classmethod
    def validate_metadata(cls, value: dict[str, Any]) -> dict[str, Any]:
        """Ensure state metadata is JSON-compatible."""
        return ensure_json_serializable(value, field_name="runtime state metadata")
