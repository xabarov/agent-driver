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
    tool_rows = [
        row for row in trimmed.prompt_messages if str(row.get("role")) == "tool"
    ]
    assert tool_rows
    stub_content = str(tool_rows[-1].get("content", "")).lower()
    assert "trimmed" in stub_content
    assert "sourced evidence" in stub_content
    assert "dropped due to context budget" not in stub_content


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


def test_trim_context_protects_assistant_antecedent_for_followup() -> None:
    """A follow-up ("those 15") must keep the assistant's own prior enumeration.

    Regression for the multi-turn anaphora-to-the-assistant's-own-answer gap:
    without protection the oldest-first char-budget pass keeps stale chit-chat
    and drops the recent assistant list, so the model can no longer bind
    "those 15" to the set it just produced. ``protect_recent_turns`` keeps the
    recent turns (user AND assistant) verbatim so the antecedent survives.
    """
    enumerated = (
        "15 meetings are about AI: M1 M2 M3 M4 M5 M6 M7 M8 "
        "M9 M10 M11 M12 M13 M14 M15"
    )
    messages = [
        {"role": "user", "content": "hi " * 400},  # stale, fills the budget
        {"role": "assistant", "content": "hello " * 400},  # stale
        {"role": "assistant", "content": enumerated},  # the antecedent
        {"role": "user", "content": "how many of those 15 are about NLP?"},
    ]
    budget = ContextBudget(max_chars=3000, max_messages=24, protect_recent_turns=2)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    contents = [str(m["content"]) for m in trimmed.prompt_messages]
    assert any(enumerated in c for c in contents), "assistant antecedent must survive"
    assert trimmed.prompt_messages[-1]["content"] == (
        "how many of those 15 are about NLP?"
    )
    assert trimmed.metadata["protect_recent_turns"] == 2
    assert trimmed.metadata["protected_messages"] == 2


def test_trim_context_without_protection_regresses_to_oldest_first() -> None:
    """Legacy behaviour is unchanged when ``protect_recent_turns`` is unset.

    Guards that the feature is inert by default: with ``None`` the oldest-first
    prefix still wins and the recent assistant antecedent is dropped, exactly as
    before this change. This is the baseline the protected path must not affect
    for existing callers that never opt in.
    """
    enumerated = "15 meetings are about AI: M1 ... M15"
    messages = [
        {"role": "user", "content": "hi " * 400},  # 1200 chars
        {"role": "assistant", "content": "hello " * 300},  # 1800 -> fills 3000 exactly
        {"role": "assistant", "content": enumerated},  # overflows -> dropped (legacy)
        {"role": "user", "content": "how many of those 15 are about NLP?"},
    ]
    budget = ContextBudget(max_chars=3000, max_messages=24)  # protect_recent_turns=None
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    contents = [str(m["content"]) for m in trimmed.prompt_messages]
    assert not any(enumerated in c for c in contents)
    assert trimmed.metadata["protect_recent_turns"] is None
    assert trimmed.metadata["protected_messages"] == 0


def test_trim_context_protects_from_that_list_reference() -> None:
    """Other antecedent phrasings ("from that list") bind to the recent turn too."""
    listed = "Owners: alice, bob, carol, dave, erin"
    messages = [
        {"role": "user", "content": "filler " * 500},
        {"role": "assistant", "content": listed},
        {"role": "user", "content": "from that list, who owns billing?"},
    ]
    budget = ContextBudget(max_chars=1500, max_messages=24, protect_recent_turns=2)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    contents = [str(m["content"]) for m in trimmed.prompt_messages]
    assert any(listed in c for c in contents)
    assert trimmed.prompt_messages[-1]["content"] == "from that list, who owns billing?"


def test_trim_context_keeps_protected_tail_even_when_it_exceeds_budget() -> None:
    """If the protected tail alone exceeds the char budget it is still kept.

    Bounded by the number of protected turns, older messages are dropped first.
    The request must never end up empty or lose the current turn.
    """
    messages = [
        {"role": "user", "content": "old " * 200},
        {"role": "assistant", "content": "A" * 4000},  # protected, over budget alone
        {"role": "user", "content": "B" * 4000},  # protected current turn
    ]
    budget = ContextBudget(max_chars=2000, max_messages=24, protect_recent_turns=2)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    contents = [str(m["content"]) for m in trimmed.prompt_messages]
    assert any(c.startswith("A" * 100) for c in contents), "protected tail kept in full"
    assert trimmed.prompt_messages[-1]["content"] == "B" * 4000
    # The stale head message is dropped to make room.
    assert not any(c.startswith("old ") for c in contents)


def test_trim_context_protection_never_drops_final_or_empties() -> None:
    """Protection preserves the never-empty / final-turn invariants."""
    messages = [
        {"role": "system", "content": "policy"},
        {"role": "user", "content": "q" * 5000},
    ]
    budget = ContextBudget(max_chars=1000, max_messages=24, protect_recent_turns=4)
    trimmed = trim_context(budget=budget, prompt_messages=messages)
    assert trimmed.prompt_messages, "request must never end up empty"
    assert trimmed.prompt_messages[-1]["role"] == "user"
    assert str(trimmed.prompt_messages[-1]["content"]).startswith("q")
