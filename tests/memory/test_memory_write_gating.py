"""Memory epic M2 — write-gating discipline shared by every write path.

A single canonical "what NOT to keep in memory" block is referenced by both the
model-callable ``remember`` tool and the automatic fact extractor, so the
discipline cannot drift between them, and it carries openclaude's key clause:
the exclusions hold even when the user explicitly asks to save.
"""

from __future__ import annotations

from agent_driver.memory.extraction import _EXTRACTION_SYSTEM_PROMPT
from agent_driver.memory.guidance import MEMORY_WRITE_GATING
from agent_driver.tools.memory import _REMEMBER_DESCRIPTION


def test_gating_covers_the_three_exclusion_categories() -> None:
    lowered = MEMORY_WRITE_GATING.lower()
    assert "secret" in lowered or "credential" in lowered  # secrets
    assert "ephemeral" in lowered or "task state" in lowered  # ephemeral state
    assert "re-derivable" in lowered or "git history" in lowered  # derivable facts


def test_gating_holds_even_when_the_user_asks_to_save() -> None:
    # openclaude's key insight: don't blindly obey "remember this".
    lowered = MEMORY_WRITE_GATING.lower()
    assert "even when" in lowered
    assert "remember this" in lowered


def test_remember_tool_uses_the_shared_gating_block() -> None:
    assert MEMORY_WRITE_GATING in _REMEMBER_DESCRIPTION


def test_extractor_uses_the_shared_gating_block() -> None:
    assert MEMORY_WRITE_GATING in _EXTRACTION_SYSTEM_PROMPT


def test_single_source_of_truth_no_drift() -> None:
    # Both write paths must reference the SAME string object content — the whole
    # point of M2 is that there is one canonical block, not two hand-copied lists.
    assert (
        MEMORY_WRITE_GATING in _REMEMBER_DESCRIPTION
        and MEMORY_WRITE_GATING in _EXTRACTION_SYSTEM_PROMPT
    )
