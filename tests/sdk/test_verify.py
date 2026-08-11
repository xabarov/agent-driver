"""Independent verifier / critic for subagent output (coordination C8)."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_driver.contracts.enums import RunStatus, SubagentJoinPolicy
from agent_driver.sdk import (
    VerifierVerdict,
    verify_answer,
    verify_subagent_group,
    verify_subagent_result,
)
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.sdk.subagent import SubagentResult

# Scripted verifier replies, consumed one per aux_completion call (FIFO), or — when
# concurrency makes FIFO order fragile — keyed by a substring of the ANSWER being verified.
_REPLIES: list[object] = []
_REPLY_BY_ANSWER: dict[str, str] = {}


def _reply(**payload: object) -> str:
    return json.dumps(payload)


async def _fake_aux(**kwargs):  # noqa: ANN003, ANN202
    if _REPLY_BY_ANSWER:
        content = kwargs["messages"][-1].content
        for needle, reply in _REPLY_BY_ANSWER.items():
            if needle in content:
                return SimpleNamespace(message=SimpleNamespace(content=reply))
        raise AssertionError(f"no scripted reply matched: {content!r}")
    item = _REPLIES.pop(0)
    if isinstance(item, BaseException):
        raise item
    return SimpleNamespace(message=SimpleNamespace(content=item))


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _REPLIES.clear()
    _REPLY_BY_ANSWER.clear()
    monkeypatch.setattr("agent_driver.llm.aux.aux_completion", _fake_aux)


def _result(agent_type: str, status: RunStatus, answer: str | None) -> SubagentResult:
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


def _group(*results: SubagentResult | None) -> SubagentGroupResult:
    return SubagentGroupResult(
        results=tuple(results),
        errors=tuple(None for _ in results),
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        satisfied=True,
    )


@pytest.mark.asyncio
async def test_accepts_a_sound_answer() -> None:
    _REPLIES.append(_reply(accepted=True, confidence=0.9, issues=[], rationale="looks correct"))
    v = await verify_answer("2+2=4", provider=object(), task="add 2 and 2")
    assert isinstance(v, VerifierVerdict)
    assert v.accepted and v.confidence == 0.9 and v.issues == ()
    assert v.rationale == "looks correct"


@pytest.mark.asyncio
async def test_rejects_and_surfaces_issues() -> None:
    _REPLIES.append(
        _reply(accepted=False, confidence=0.8, issues=["wrong total", "ignored units"], rationale="math error")
    )
    v = await verify_answer("2+2=5", provider=object(), task="add")
    assert not v.accepted
    assert v.issues == ("wrong total", "ignored units")


@pytest.mark.asyncio
async def test_empty_answer_rejected_without_model_call() -> None:
    v = await verify_answer("   ", provider=object(), task="t")
    assert not v.accepted and v.confidence == 1.0 and v.rationale == "empty-answer"
    assert _REPLIES == []  # no aux call was consumed


@pytest.mark.asyncio
async def test_verifier_outage_degrades_to_unverified() -> None:
    _REPLIES.append(RuntimeError("provider down"))
    v = await verify_answer("something", provider=object(), task="t")
    # non-blocking: accepted but with zero confidence, flagged in the rationale
    assert v.accepted and v.confidence == 0.0
    assert v.rationale.startswith("verifier-unavailable")


@pytest.mark.asyncio
async def test_unparseable_reply_is_unverified() -> None:
    _REPLIES.append("not json at all")
    v = await verify_answer("x", provider=object(), task="t")
    assert v.accepted and v.confidence == 0.0
    assert v.rationale == "unparseable-verifier-reply"


@pytest.mark.asyncio
async def test_confidence_is_clamped() -> None:
    _REPLIES.append(_reply(accepted=True, confidence=5.0, issues=[], rationale="ok"))
    v = await verify_answer("x", provider=object(), task="t")
    assert v.confidence == 1.0


@pytest.mark.asyncio
async def test_quorum_majority_accepts_and_unions_issues() -> None:
    _REPLIES.extend(
        [
            _reply(accepted=True, confidence=0.9, issues=["nit-a"], rationale="ok"),
            _reply(accepted=True, confidence=0.7, issues=["nit-b"], rationale="fine"),
            _reply(accepted=False, confidence=0.6, issues=["nit-a", "real"], rationale="doubt"),
        ]
    )
    v = await verify_answer("x", provider=object(), task="t", votes=3)
    assert v.accepted  # 2 of 3 accept
    assert set(v.issues) == {"nit-a", "nit-b", "real"}  # unioned, deduped
    assert v.confidence == pytest.approx((0.9 + 0.7 + 0.6) / 3)


@pytest.mark.asyncio
async def test_quorum_majority_rejects() -> None:
    _REPLIES.extend(
        [
            _reply(accepted=False, confidence=0.8, issues=["a"], rationale="no"),
            _reply(accepted=False, confidence=0.7, issues=["b"], rationale="nope"),
            _reply(accepted=True, confidence=0.9, issues=[], rationale="yes"),
        ]
    )
    v = await verify_answer("x", provider=object(), task="t", votes=3)
    assert not v.accepted  # 2 of 3 reject
    assert v.rationale in {"no", "nope"}  # a dissenting (rejecting) rationale


@pytest.mark.asyncio
async def test_verify_subagent_result_none_is_rejected() -> None:
    v = await verify_subagent_result(None, provider=object(), task="t")
    assert not v.accepted and v.rationale == "no-result"
    assert _REPLIES == []


@pytest.mark.asyncio
async def test_verify_subagent_result_checks_answer() -> None:
    _REPLIES.append(_reply(accepted=True, confidence=0.8, issues=[], rationale="good"))
    res = _result("w", RunStatus.COMPLETED, "the answer")
    v = await verify_subagent_result(res, provider=object(), task="t")
    assert v.accepted


@pytest.mark.asyncio
async def test_verify_subagent_group_aligns_to_results() -> None:
    _REPLY_BY_ANSWER.update(
        {
            "good answer": _reply(accepted=True, confidence=0.9, issues=[], rationale="ok"),
            "bad answer": _reply(accepted=False, confidence=0.7, issues=["bad"], rationale="no"),
        }
    )
    group = _group(
        _result("a", RunStatus.COMPLETED, "good answer"),
        _result("b", RunStatus.COMPLETED, "bad answer"),
        None,  # a missing child → deterministic reject, no aux call
    )
    verdicts = await verify_subagent_group(group, provider=object(), task="t")
    assert len(verdicts) == 3
    assert verdicts[0].accepted is True
    assert verdicts[1].accepted is False and verdicts[1].issues == ("bad",)
    assert verdicts[2].accepted is False and verdicts[2].rationale == "no-result"
