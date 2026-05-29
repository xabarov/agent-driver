"""Deterministic observation microcompaction tests."""

from __future__ import annotations

from agent_driver.context.microcompaction import microcompact_observations


def test_microcompaction_preserves_recent_and_compacts_old_large_rows() -> None:
    """Older large observations should be replaced with deterministic stub."""
    observations = [
        {
            "observation_id": "obs_1",
            "text_preview": "x" * 400,
            "provenance": {"source": "tool_stdout", "tool_call_id": "call_1"},
            "metadata": {},
        },
        {
            "observation_id": "obs_2",
            "text_preview": "short",
            "provenance": {"source": "tool_log", "tool_call_id": "call_2"},
            "metadata": {},
        },
        {
            "observation_id": "obs_3",
            "text_preview": "y" * 260,
            "provenance": {"source": "tool_stderr", "tool_call_id": "call_3"},
            "metadata": {},
        },
    ]
    result = microcompact_observations(
        observations, preserve_recent=2, max_preview_chars=120
    )
    assert "output compacted" in result.observations[0]["text_preview"]
    assert result.observations[1]["text_preview"] == "short"
    assert result.observations[2]["text_preview"] == "y" * 260
    assert result.bytes_saved > 0
    assert result.estimated_tokens_saved > 0
    assert result.audit[0]["tool_call_id"] == "call_1"


def test_microcompaction_is_deterministic() -> None:
    """Same observations should produce identical microcompaction output."""
    observations = [
        {
            "observation_id": "obs_1",
            "text_preview": "z" * 256,
            "provenance": {"source": "tool_log", "tool_call_id": "call_1"},
            "metadata": {},
        }
    ]
    first = microcompact_observations(observations, preserve_recent=0)
    second = microcompact_observations(observations, preserve_recent=0)
    assert first.observations == second.observations
    assert first.audit == second.audit
    assert first.bytes_saved == second.bytes_saved
