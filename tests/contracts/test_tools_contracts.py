"""Tool governance contract tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    ToolCall,
    ToolError,
    ToolManifest,
    ToolPolicyDecision,
    ToolResultEnvelope,
)


def test_tool_manifest_defaults() -> None:
    """Manifest should expose stable defaults for governance layer."""
    manifest = ToolManifest(name="lookup", description="Lookup document")
    assert manifest.timeout_seconds == 30.0
    assert manifest.output_char_budget == 4000
    assert manifest.idempotent


def test_tool_manifest_rejects_non_positive_budget() -> None:
    """Reject non-positive output budget."""
    with pytest.raises(ValidationError):
        ToolManifest(name="lookup", description="Lookup document", output_char_budget=0)


def test_tool_result_envelope_deny_requires_error() -> None:
    """Deny result should include structured tool error."""
    with pytest.raises(ValidationError):
        ToolResultEnvelope(
            call=ToolCall(tool_name="lookup"),
            decision=ToolPolicyDecision.DENY,
        )


def test_tool_result_envelope_interrupt_requires_payload() -> None:
    """Interrupt result should include interrupt payload."""
    with pytest.raises(ValidationError):
        ToolResultEnvelope(
            call=ToolCall(tool_name="lookup"),
            decision=ToolPolicyDecision.INTERRUPT,
        )


def test_tool_result_envelope_round_trip() -> None:
    """Envelope should round-trip through JSON-safe structure."""
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="lookup", args={"query": "hi"}),
        summary="ok",
        structured_output={"value": 1},
        metadata={"source": "test"},
    )
    restored = ToolResultEnvelope.model_validate(envelope.model_dump(mode="json"))
    assert restored.summary == "ok"
    assert restored.structured_output == {"value": 1}
    assert restored.error is None


def test_tool_error_metadata_validation() -> None:
    """ToolError validates JSON-compatible metadata."""
    err = ToolError(code="x", message="bad", metadata={"attempt": 1})
    assert err.metadata["attempt"] == 1
