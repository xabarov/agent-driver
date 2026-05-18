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
