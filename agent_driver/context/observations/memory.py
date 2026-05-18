"""Observation memory helpers for bounded previews."""

from __future__ import annotations

from uuid import uuid4

from agent_driver.contracts.context import ObservationMemory, ObservationProvenance
from agent_driver.contracts.enums import ObservationSource, ObservationTrust


def build_observation_memory(  # pylint: disable=too-many-arguments
    *,
    text: str,
    source: ObservationSource,
    trust: ObservationTrust,
    max_chars: int,
    tool_name: str | None = None,
    tool_call_id: str | None = None,
    event_id: str | None = None,
) -> ObservationMemory:
    """Create bounded observation preview with provenance/trust metadata."""
    truncated = len(text) > max_chars
    preview = text[:max_chars] + ("..." if truncated else "")
    return ObservationMemory(
        observation_id=f"obs_{uuid4().hex}",
        text_preview=preview,
        truncated=truncated,
        original_length=len(text),
        provenance=ObservationProvenance(
            source=source,
            trust=trust,
            tool_name=tool_name,
            tool_call_id=tool_call_id,
            event_id=event_id,
        ),
        metadata={"max_chars": max_chars},
    )
