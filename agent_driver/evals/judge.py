"""Generic answer-quality judge for the eval harness (domain-neutral).

The deterministic evaluators score *runtime invariants* (event schema, terminal
state, budgets) and the rubric evaluator scores an answer against caller-supplied
literals — but neither judges open-ended answer *quality* ("did this actually
address the request well?"). That gap matters for the R-track multi-model work:
routing a turn to a cheaper model may keep success-status and economics identical
while quietly lowering answer quality, and a success-only A/B can't see it.

This module adds an opt-in LLM judge. It is deliberately domain-neutral — the
SDK provides the *mechanism* (a small model scores answer-vs-prompt on a generic
0–10 rubric); any domain rubric stays in the consumer. It mirrors the R8
``LlmDifficultyRouter`` pattern: one cache-safe :func:`aux_completion` call, a
robust parse, and a graceful fallback so a judge outage never breaks a run.
"""

from __future__ import annotations

import json
import re
from typing import Any, Protocol, runtime_checkable

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.messages import ChatMessage

_JUDGE_SYSTEM = (
    "You are a strict answer-quality judge. You are given a user REQUEST and a "
    "candidate ANSWER produced by an AI agent. Rate how well the ANSWER satisfies "
    "the REQUEST on a 0–10 integer scale:\n"
    "10 = fully correct, complete, and directly responsive;\n"
    "5 = partially correct or incomplete;\n"
    "0 = wrong, empty, off-topic, or a refusal.\n"
    "Judge only the ANSWER's fitness for the REQUEST — not its style or length. "
    'Reply with ONLY a JSON object: {"score": <0-10 integer>, "rationale": '
    '"<one short sentence>"}. No prose, no code fences.'
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_NUMBER_RE = re.compile(r"-?\d+(?:\.\d+)?")


class JudgeVerdict(ContractModel):
    """One judge score for an answer, normalized to ``[0, 1]``."""

    score: float = 0.0
    rationale: str = ""


@runtime_checkable
class AnswerJudge(Protocol):
    """Scores a single ``(prompt, answer)`` pair for answer quality."""

    async def score(self, *, prompt: str, answer: str) -> JudgeVerdict:
        """Return a :class:`JudgeVerdict` with ``score`` in ``[0, 1]``."""
        ...


def _parse_verdict(raw: str) -> JudgeVerdict:
    """Parse a judge reply into a 0–1 verdict, tolerant of noisy formatting."""
    text = (raw or "").strip()
    match = _JSON_OBJ_RE.search(text)
    if match:
        try:
            data = json.loads(match.group(0))
        except (json.JSONDecodeError, ValueError):
            data = None
        if isinstance(data, dict) and "score" in data:
            try:
                raw_score = float(data["score"])
            except (TypeError, ValueError):
                raw_score = 0.0
            rationale = data.get("rationale")
            return JudgeVerdict(
                score=_normalize(raw_score),
                rationale=str(rationale) if isinstance(rationale, str) else "",
            )
    # Fallback: first bare number in the reply (a model that ignored the JSON ask).
    number = _NUMBER_RE.search(text)
    if number:
        try:
            return JudgeVerdict(score=_normalize(float(number.group(0))))
        except ValueError:
            pass
    return JudgeVerdict(score=0.0, rationale="unparseable-judge-reply")


def _normalize(raw_score: float) -> float:
    """Map a 0–10 (or already-0–1) score into a clamped ``[0, 1]`` float."""
    scale = raw_score / 10.0 if raw_score > 1.0 else raw_score
    return max(0.0, min(1.0, scale))


class LlmJudge:
    """Answer-quality judge backed by a small, cheap model.

    ``provider`` is any ``LlmProvider`` and ``model`` should point at a fast, low
    cost model (a ``*-flash-lite`` / ``*-nano`` / small open-weight) — the judge
    emits a tiny JSON verdict, so latency dominates. On any error the verdict is a
    conservative ``0.0`` (a judge outage must never crash an A/B), tagged in the
    rationale so it is distinguishable from a genuine zero.
    """

    def __init__(
        self,
        *,
        provider: Any,
        model: str | None = None,
        max_input_chars: int = 6000,
    ) -> None:
        self._provider = provider
        self._model = model
        self._max_input_chars = max_input_chars

    async def score(self, *, prompt: str, answer: str) -> JudgeVerdict:
        """Judge one answer; never raises (returns a 0.0 verdict on failure)."""
        if not (answer or "").strip():
            return JudgeVerdict(score=0.0, rationale="empty-answer")
        cap = self._max_input_chars
        user = f"REQUEST:\n{prompt[:cap]}\n\nANSWER:\n{answer[:cap]}"
        try:
            from agent_driver.llm.aux import aux_completion

            resp = await aux_completion(
                provider=self._provider,
                model=self._model,
                task="answer_judge",
                temperature=0.0,
                max_tokens=200,
                messages=[
                    ChatMessage(role="system", content=_JUDGE_SYSTEM),
                    ChatMessage(role="user", content=user),
                ],
            )
        except Exception as exc:  # noqa: BLE001 — a judge call must never break an A/B
            return JudgeVerdict(score=0.0, rationale=f"judge-error:{type(exc).__name__}")
        return _parse_verdict(resp.message.content or "")


async def judge_trajectories(
    trajectories: list[Any],
    judge: AnswerJudge,
    *,
    prompt_by_item: dict[str, str] | None = None,
) -> list[Any]:
    """Score each trajectory's ``answer`` and stash it in ``metadata['quality_score']``.

    Mutates and returns the same trajectories (so the aggregate step can read the
    score just like it reads policy/trace metadata). Trajectories with no answer
    are left unscored — they contribute nothing to the quality distribution rather
    than a misleading zero. ``prompt_by_item`` maps ``item_id`` → the request text
    the answer was produced for; when absent the trajectory's stored answer is
    judged against an empty request.
    """
    import asyncio

    lookup = prompt_by_item or {}
    scored = [t for t in trajectories if getattr(t, "answer", None)]

    async def _one(traj: Any) -> None:
        verdict = await judge.score(
            prompt=lookup.get(getattr(traj, "item_id", ""), ""),
            answer=traj.answer or "",
        )
        traj.metadata["quality_score"] = verdict.score
        traj.metadata.setdefault("quality_rationale", verdict.rationale)

    if scored:
        await asyncio.gather(*(_one(t) for t in scored))
    return trajectories


__all__ = [
    "AnswerJudge",
    "JudgeVerdict",
    "LlmJudge",
    "judge_trajectories",
]
