"""Observation memory tests."""

from __future__ import annotations

from agent_driver.context.observations import build_observation_memory
from agent_driver.contracts import ObservationSource, ObservationTrust


def test_build_observation_memory_truncates_with_metadata() -> None:
    """Helper should truncate long text and preserve provenance/trust labels."""
    observation = build_observation_memory(
        text="x" * 32,
        source=ObservationSource.TOOL_STDOUT,
        trust=ObservationTrust.HIGH,
        max_chars=8,
        tool_name="lookup",
        tool_call_id="call_1",
        event_id="evt_1",
    )
    assert observation.truncated is True
    assert observation.text_preview.endswith("...")
    assert observation.provenance.tool_name == "lookup"
    assert observation.provenance.trust == ObservationTrust.HIGH


def test_build_observation_memory_round_trip() -> None:
    """Observation memory should round-trip with JSON-safe metadata."""
    observation = build_observation_memory(
        text="short",
        source=ObservationSource.TOOL_LOG,
        trust=ObservationTrust.MEDIUM,
        max_chars=32,
    )
    restored = type(observation).model_validate(observation.model_dump(mode="json"))
    assert restored.truncated is False
    assert restored.original_length == 5
