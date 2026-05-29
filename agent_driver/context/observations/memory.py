"""Observation memory helpers for bounded previews."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import uuid4

from agent_driver.contracts.context import ObservationMemory, ObservationProvenance
from agent_driver.contracts.enums import ObservationSource, ObservationTrust


@dataclass(frozen=True, slots=True)
class ObservationMemoryInput:
    """Input payload for building one bounded observation preview."""

    text: str
    source: ObservationSource
    trust: ObservationTrust
    max_chars: int
    tool_name: str | None = None
    tool_call_id: str | None = None
    event_id: str | None = None


def build_observation_memory_from_input(
    observation: ObservationMemoryInput,
) -> ObservationMemory:
    """Create bounded observation preview with provenance/trust metadata."""
    truncated = len(observation.text) > observation.max_chars
    preview = observation.text[: observation.max_chars] + ("..." if truncated else "")
    return ObservationMemory(
        observation_id=f"obs_{uuid4().hex}",
        text_preview=preview,
        truncated=truncated,
        original_length=len(observation.text),
        provenance=ObservationProvenance(
            source=observation.source,
            trust=observation.trust,
            tool_name=observation.tool_name,
            tool_call_id=observation.tool_call_id,
            event_id=observation.event_id,
        ),
        metadata={"max_chars": observation.max_chars},
    )


def build_observation_memory(
    *,
    text: str,
    source: ObservationSource,
    trust: ObservationTrust,
    max_chars: int,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    event_id: str | None = None,
) -> ObservationMemory:
    """Backward-compatible wrapper over ObservationMemoryInput contract."""
    return build_observation_memory_from_input(
        ObservationMemoryInput(
            text=text,
            source=source,
            trust=trust,
            max_chars=max_chars,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            event_id=event_id,
        )
    )


__all__ = [
    "ObservationMemoryInput",
    "build_observation_memory",
    "build_observation_memory_from_input",
]
