"""SDK S3: config settings, rubric-state read, and event tool-name extractor
are reachable from the public ``agent_driver.runtime`` facade (no reach-ins)."""

from __future__ import annotations

from agent_driver.contracts import RuntimeEventType, new_runtime_event


def test_compaction_and_trimming_settings_public_and_configure_runner() -> None:
    """CompactionSettings/TrimmingSettings are public and accepted by RunnerConfig."""
    from agent_driver.runtime import (
        CompactionSettings,
        RunnerConfig,
        TrimmingSettings,
    )

    trimming = TrimmingSettings()
    compaction = CompactionSettings()
    config = RunnerConfig(trimming=trimming, compaction=compaction)
    assert config.trimming is trimming
    assert config.compaction is compaction


def test_get_rubric_runtime_state_is_public() -> None:
    """The rubric-state reader + its return type are on the public facade."""
    from agent_driver.runtime import RubricRuntimeState, get_rubric_runtime_state

    assert callable(get_rubric_runtime_state)
    assert isinstance(RubricRuntimeState, type)


def _tool_event(seq: int, payload: dict[str, object]) -> object:
    return new_runtime_event(
        event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
        context={"run_id": "r", "attempt_id": "a", "seq": seq},
        options={"payload": payload},
    )


def test_tool_name_from_event_handles_both_payload_shapes() -> None:
    """tool_name_from_event reads the tools-list and flat shapes, None otherwise."""
    from agent_driver.runtime import tool_name_from_event

    nested = _tool_event(1, {"tools": [{"tool_name": "web_search", "status": "ok"}]})
    flat = _tool_event(2, {"tool_name": "read"})
    named = _tool_event(3, {"tools": [{"name": "write"}]})
    non_tool = new_runtime_event(
        event_type=RuntimeEventType.RUN_STARTED,
        context={"run_id": "r", "attempt_id": "a", "seq": 4},
    )

    assert tool_name_from_event(nested) == "web_search"
    assert tool_name_from_event(flat) == "read"
    assert tool_name_from_event(named) == "write"
    assert tool_name_from_event(non_tool) is None
