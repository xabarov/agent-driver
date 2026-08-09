"""R6 — pluggable model router: the heuristic difficulty router + the ModelRouter seam."""

from __future__ import annotations

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.model_router import (
    HeuristicDifficultyRouter,
    ModelRouter,
    last_user_text,
)

_RUN = AgentRunInput(input="x", agent_id="a", graph_preset="single_react")


def _route(router, text):
    return router.route(
        messages=[{"role": "user", "content": text}],
        run_input=_RUN,
        default_role="default",
    )


def test_last_user_text_picks_latest_user_message():
    msgs = [
        {"role": "user", "content": "first"},
        {"role": "assistant", "content": "reply"},
        {"role": "user", "content": "second"},
    ]
    assert last_user_text(msgs) == "second"


def test_last_user_text_empty_when_no_user():
    assert last_user_text([{"role": "system", "content": "s"}]) == ""


def test_heuristic_is_a_model_router():
    assert isinstance(HeuristicDifficultyRouter(), ModelRouter)


def test_short_plain_turn_is_simple():
    assert _route(HeuristicDifficultyRouter(), "what is 2 + 2?") == "simple"


@pytest.mark.parametrize(
    "text",
    [
        "plan the migration",
        "please refactor this module",
        "debug the failing test",
        "analyze the trade-off here",
    ],
)
def test_strong_keyword_routes_to_strong(text):
    assert _route(HeuristicDifficultyRouter(), text) == "strong"


def test_code_block_routes_to_strong():
    assert _route(HeuristicDifficultyRouter(), "fix:\n```\nx=1\n```") == "strong"


def test_long_turn_routes_to_strong():
    assert _route(HeuristicDifficultyRouter(), "word " * 60) == "strong"


def test_empty_turn_returns_default_role():
    assert _route(HeuristicDifficultyRouter(), "   ") == "default"


def test_custom_roles_and_thresholds():
    router = HeuristicDifficultyRouter(
        simple_role="cheap",
        strong_role="smart",
        strong_keywords=("wibble",),
        simple_max_words=2,
    )
    assert _route(router, "hi there") == "cheap"
    assert _route(router, "one two three") == "smart"  # over word threshold
    assert _route(router, "please wibble it") == "smart"  # custom keyword
    assert _route(router, "plan it") == "cheap"  # default keyword no longer strong
