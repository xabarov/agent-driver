"""Observation extraction from tool execution envelopes."""

from __future__ import annotations

from typing import Any

from agent_driver.context import build_observation_memory
from agent_driver.contracts.enums import ObservationSource, ObservationTrust
from agent_driver.runtime.tools import ToolExecutionResult


def build_observations_from_tool_result(
    result: ToolExecutionResult,
    *,
    observation_max_chars: int,
) -> list[dict[str, Any]]:
    """Build bounded observation rows from tool envelopes."""
    observations: list[dict[str, Any]] = []
    for envelope in result.envelopes:
        if envelope.summary is None:
            continue
        observation = build_observation_memory(
            text=envelope.summary,
            source=ObservationSource.TOOL_LOG,
            trust=ObservationTrust.UNVERIFIED,
            max_chars=observation_max_chars,
            tool_name=envelope.call.tool_name,
            tool_call_id=envelope.call.tool_call_id,
        )
        observations.append(observation.model_dump(mode="json"))
        structured = envelope.structured_output
        if not isinstance(structured, dict):
            continue
        raw_observations = structured.get("observations")
        if not isinstance(raw_observations, list):
            continue
        for row in raw_observations:
            if not isinstance(row, dict):
                continue
            preview = row.get("text_preview")
            source_raw = row.get("source")
            if not isinstance(preview, str):
                continue
            source_map = {
                "stdout": ObservationSource.TOOL_STDOUT,
                "stderr": ObservationSource.TOOL_STDERR,
            }
            source = source_map.get(str(source_raw).lower(), ObservationSource.TOOL_LOG)
            extra_observation = build_observation_memory(
                text=preview,
                source=source,
                trust=ObservationTrust.UNVERIFIED,
                max_chars=observation_max_chars,
                tool_name=envelope.call.tool_name,
                tool_call_id=envelope.call.tool_call_id,
            )
            observations.append(extra_observation.model_dump(mode="json"))
    return observations


__all__ = ["build_observations_from_tool_result"]
