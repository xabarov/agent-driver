"""Merge / synthesize subagent-group results (coordination C1)."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import RunStatus, SubagentMergeMode
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import (
    merge_subagent_results,
    synthesize_subagent_results,
)
from agent_driver.sdk.subagent import SubagentResult


def _r(agent_type: str, answer: str | None, status: RunStatus = RunStatus.COMPLETED):
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=None,
        answer=answer,
        structured_output=None,
        tool_trace=(),
        usage=None,
        raw_output=None,
    )


def test_append_labels_and_skips_non_completed() -> None:
    out = merge_subagent_results(
        [_r("a", "alpha"), _r("b", "beta"), _r("c", None, RunStatus.FAILED)],
        mode=SubagentMergeMode.APPEND,
    )
    assert out == "[a] alpha\n\n[b] beta"  # failed child excluded


def test_append_without_labels() -> None:
    out = merge_subagent_results(
        [_r("a", "alpha"), _r("b", "beta")], mode=SubagentMergeMode.APPEND, label=False
    )
    assert out == "alpha\n\nbeta"


def test_vote_returns_plurality_answer() -> None:
    out = merge_subagent_results(
        [_r("a", "42"), _r("b", "43"), _r("c", "42")], mode=SubagentMergeMode.VOTE
    )
    assert out == "42"


def test_rank_orders_longest_first() -> None:
    out = merge_subagent_results(
        [_r("a", "x"), _r("b", "a longer answer")],
        mode=SubagentMergeMode.RANK,
        label=False,
    )
    assert out.splitlines()[0] == "a longer answer"


def test_manual_returns_review_stub() -> None:
    out = merge_subagent_results(
        [_r("a", "x"), _r("b", "y")], mode=SubagentMergeMode.MANUAL
    )
    assert "manual review required: 2" in out


def test_max_items_and_max_chars_bound_the_merge() -> None:
    results = [_r(f"a{i}", "answer") for i in range(5)]
    out = merge_subagent_results(
        results, mode=SubagentMergeMode.APPEND, max_items=2, label=False
    )
    assert out == "answer\n\nanswer"
    assert len(merge_subagent_results(results, max_chars=4)) == 4


def test_empty_results_merge_to_empty() -> None:
    assert merge_subagent_results([]) == ""
    assert merge_subagent_results([_r("a", None, RunStatus.FAILED)]) == ""


def test_synthesize_mode_rejected_by_sync_merge() -> None:
    with pytest.raises(ValueError):
        merge_subagent_results([_r("a", "x")], mode=SubagentMergeMode.SYNTHESIZE)


@pytest.mark.asyncio
async def test_synthesize_runs_an_llm_over_the_answers() -> None:
    out = await synthesize_subagent_results(
        [_r("a", "The sky is blue."), _r("b", "The sky appears blue.")],
        provider=FakeProvider(response_text="The sky is blue."),
    )
    assert out == "The sky is blue."


@pytest.mark.asyncio
async def test_synthesize_shortcuts_single_and_empty() -> None:
    only = await synthesize_subagent_results(
        [_r("a", "only")], provider=FakeProvider(response_text="unused")
    )
    assert only == "only"  # no model call for one answer
    empty = await synthesize_subagent_results(
        [], provider=FakeProvider(response_text="unused")
    )
    assert empty == ""


class _BoomProvider:
    async def complete(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("down")

    async def stream(self, request):  # noqa: ANN001, ANN201
        raise RuntimeError("down")
        yield  # pragma: no cover


@pytest.mark.asyncio
async def test_synthesize_degrades_to_append_on_provider_error() -> None:
    out = await synthesize_subagent_results(
        [_r("a", "alpha"), _r("b", "beta")], provider=_BoomProvider()
    )
    assert out == "[a] alpha\n\n[b] beta"  # fell back to APPEND, did not raise


# --- partial-output salvage (C3) -------------------------------------------- #


def test_default_merge_excludes_partial_non_completed_answers() -> None:
    out = merge_subagent_results(
        [_r("a", "done"), _r("b", "partial", RunStatus.TIMED_OUT)]
    )
    assert out == "[a] done"  # timed-out child dropped by default


def test_include_partial_salvages_non_completed_answers_labeled() -> None:
    out = merge_subagent_results(
        [_r("a", "done"), _r("b", "partial", RunStatus.TIMED_OUT)],
        include_partial=True,
    )
    assert out == "[a] done\n\n[b (partial: timed_out)] partial"


def test_include_partial_still_skips_empty_answers() -> None:
    out = merge_subagent_results(
        [_r("a", "done"), _r("b", None, RunStatus.FAILED)], include_partial=True
    )
    assert out == "[a] done"  # failed child had no answer to salvage


@pytest.mark.asyncio
async def test_synthesize_can_include_partial() -> None:
    out = await synthesize_subagent_results(
        [_r("a", "done"), _r("b", "partial", RunStatus.TIMED_OUT)],
        provider=FakeProvider(response_text="combined"),
        include_partial=True,
    )
    assert out == "combined"  # two contributors (1 completed + 1 partial) → synthesized
