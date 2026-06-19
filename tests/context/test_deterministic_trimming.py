"""Deterministic context trimming tests."""

from __future__ import annotations

from agent_driver.context.trimming import trim_context
from agent_driver.contracts import ContextBudget
from agent_driver.contracts.enums import TrimAction


def test_trim_context_is_deterministic_for_same_input() -> None:
    """Trimming should produce stable output for same input and budget."""
    messages = [
        {"role": "user", "content": "a" * 10},
        {"role": "assistant", "content": "b" * 10},
        {"role": "user", "content": "c" * 10},
    ]
    budget = ContextBudget(max_chars=15, max_messages=2)
    first = trim_context(
        budget=budget,
        prompt_messages=messages,
        digest_ids=["dig_1", "dig_2"],
        artifact_ids=["art_1"],
    )
    second = trim_context(
        budget=budget,
        prompt_messages=messages,
        digest_ids=["dig_1", "dig_2"],
        artifact_ids=["art_1"],
    )
    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_trim_context_uses_digest_before_drop() -> None:
    """Trimming should mark overflow messages as digested when digests exist."""
    trimmed = trim_context(
        budget=ContextBudget(max_chars=5),
        prompt_messages=[{"role": "user", "content": "x" * 12}],
        digest_ids=["dig_1"],
        artifact_ids=[],
    )
    assert trimmed.retained_digest_ids == ["dig_1"]
    assert trimmed.audit[0].action == TrimAction.DIGESTED


def test_trim_context_uses_artifact_when_digest_missing() -> None:
    """Trimming should fallback to artifact replacement for overflow content."""
    trimmed = trim_context(
        budget=ContextBudget(max_chars=5),
        prompt_messages=[{"role": "user", "content": "x" * 12}],
        digest_ids=[],
        artifact_ids=["art_1"],
    )
    assert trimmed.retained_artifact_ids == ["art_1"]
    assert trimmed.audit[0].action == TrimAction.REPLACED_WITH_ARTIFACT


def test_trim_context_limits_observations_deterministically() -> None:
    """Trimming should keep only newest observations under max_observations."""
    observations = [
        {
            "observation_id": "obs_1",
            "text_preview": "one",
            "provenance": {"source": "tool_log", "tool_call_id": "call_1"},
        },
        {
            "observation_id": "obs_2",
            "text_preview": "two",
            "provenance": {"source": "tool_stdout", "tool_call_id": "call_2"},
        },
        {
            "observation_id": "obs_3",
            "text_preview": "three",
            "provenance": {"source": "tool_stderr", "tool_call_id": "call_3"},
        },
    ]
    trimmed = trim_context(
        budget=ContextBudget(max_chars=200, max_observations=2),
        prompt_messages=[{"role": "user", "content": "task"}],
        observation_rows=observations,
    )
    assert trimmed.metadata["input_observations"] == 3
    assert trimmed.metadata["kept_observations"] == 2
    assert trimmed.metadata["dropped_observations"] == 1
    retained = trimmed.metadata["retained_observations"]
    assert [item["observation_id"] for item in retained] == ["obs_2", "obs_3"]
    dropped = [
        item
        for item in trimmed.audit
        if item.kind == "observation" and item.action == TrimAction.DROPPED
    ]
    assert len(dropped) == 1
    assert dropped[0].reason == "max_observations_exceeded"
    kept = [
        item
        for item in trimmed.audit
        if item.kind == "observation" and item.action == TrimAction.KEPT
    ]
    assert len(kept) == 2


def test_trim_context_keeps_stub_for_latest_tool_message() -> None:
    """Latest tool message should not disappear without a stub under budget pressure."""
    messages = [
        {"role": "user", "content": "x" * 40},
        {"role": "assistant", "content": "y" * 40},
        {"role": "tool", "name": "glob_search", "content": "z" * 120},
    ]
    trimmed = trim_context(
        budget=ContextBudget(max_chars=60, max_messages=5),
        prompt_messages=messages,
        digest_ids=[],
        artifact_ids=[],
    )
    tool_rows = [row for row in trimmed.prompt_messages if str(row.get("role")) == "tool"]
    assert tool_rows
    assert "trimmed" in str(tool_rows[-1].get("content", "")).lower()


def test_trim_context_truncates_oversized_last_message_instead_of_dropping() -> None:
    """A single oversized current-turn message must be truncated, never dropped to empty.

    Regression: dropping it left zero messages, which providers reject with
    "Input required: specify 'prompt' or 'messages'".
    """
    messages = [{"role": "user", "content": "я" * 8000}]
    budget = ContextBudget(max_chars=6000, max_messages=24)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    assert len(trimmed.prompt_messages) == 1
    assert trimmed.prompt_messages[0]["role"] == "user"
    assert 0 < len(str(trimmed.prompt_messages[0]["content"])) <= 6100
    assert any(record.action == TrimAction.TRUNCATED for record in trimmed.audit)


def test_trim_context_preserves_last_message_with_history() -> None:
    """The final turn survives (truncated) even when earlier messages are dropped to make room."""
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "old turn"},
        {"role": "user", "content": "я" * 9000},
    ]
    budget = ContextBudget(max_chars=6000, max_messages=24)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    assert trimmed.prompt_messages, "request must never end up empty"
    assert trimmed.prompt_messages[-1]["role"] == "user"
    assert len(str(trimmed.prompt_messages[-1]["content"])) > 0
