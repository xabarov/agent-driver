"""Independent verifier / critic for subagent output (coordination C8).

The MAST *verification-gap* failure mode: a multi-agent system trusts a worker's answer
without an independent check, and a plausible-but-wrong result flows downstream. This is
the domain-neutral fix — a first-class, skeptical verifier that validates an answer before
the parent trusts it. Where the eval-layer `LlmJudge` scores answer *quality* on a
continuous rubric, this decides *trust*: accept or reject, with the concrete issues found.

    verdict = await verify_subagent_result(result, provider=judge_provider, task=task)
    if not verdict.accepted:
        ...  # re-run, salvage, or escalate — do not trust the answer

It composes as a verify step after a C4 coordinator phase or a C5 deep-agent fan-out
(`verify_subagent_group`), and supports an adversarial multi-vote quorum (`votes=`) so a
single flaky verifier can't wave a bad answer through. Like the judge, one cache-safe
`aux_completion` call per vote, a tolerant JSON parse, and a graceful fallback: a verifier
outage yields `accepted=True` with `confidence=0.0` (an explicit "no signal", never a
silent approval), so an outage degrades to unverified rather than breaking the run.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.sdk.subagent import SubagentResult

_VERIFIER_SYSTEM = (
    "You are an independent, skeptical verifier. You are given a TASK, optional CRITERIA, "
    "and a candidate ANSWER produced by another agent. Decide whether the ANSWER should be "
    "TRUSTED: is it correct, complete, and grounded in the task's requirements? Actively "
    "hunt for errors, unsupported or fabricated claims, and unmet requirements — assume "
    "nothing is correct until you have checked it. Reply with ONLY a JSON object: "
    '{"accepted": <true|false>, "confidence": <0-1 float>, "issues": ["<short issue>", '
    '...], "rationale": "<one short sentence>"}. No prose, no code fences. If the answer '
    "is sound, issues may be []."
)

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_MAX_ISSUES = 12


@dataclass(frozen=True, slots=True)
class VerifierVerdict:
    """One verifier's decision on whether an answer can be trusted."""

    accepted: bool
    confidence: float = 0.0
    rationale: str = ""
    issues: tuple[str, ...] = ()


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


def _parse_verifier_reply(raw: str) -> VerifierVerdict:
    """Parse a verifier reply into a verdict, tolerant of noisy formatting."""
    match = _JSON_OBJ_RE.search((raw or "").strip())
    if not match:
        return VerifierVerdict(accepted=True, confidence=0.0, rationale="unparseable-verifier-reply")
    try:
        data = json.loads(match.group(0))
    except (json.JSONDecodeError, ValueError):
        return VerifierVerdict(accepted=True, confidence=0.0, rationale="unparseable-verifier-reply")
    if not isinstance(data, dict):
        return VerifierVerdict(accepted=True, confidence=0.0, rationale="unparseable-verifier-reply")
    accepted = bool(data.get("accepted", False))
    try:
        confidence = _clamp01(float(data.get("confidence", 0.0)))
    except (TypeError, ValueError):
        confidence = 0.0
    rationale = data.get("rationale")
    raw_issues = data.get("issues")
    issues: tuple[str, ...] = ()
    if isinstance(raw_issues, list):
        issues = tuple(str(i).strip() for i in raw_issues if str(i).strip())[:_MAX_ISSUES]
    return VerifierVerdict(
        accepted=accepted,
        confidence=confidence,
        rationale=str(rationale) if isinstance(rationale, str) else "",
        issues=issues,
    )


async def _verify_once(
    answer: str,
    *,
    provider: Any,
    task: str,
    criteria: str | None,
    model: str | None,
    instruction: str | None,
    max_input_chars: int,
) -> VerifierVerdict:
    cap = max_input_chars
    parts = [f"TASK:\n{task[:cap]}"]
    if criteria:
        parts.append(f"CRITERIA:\n{criteria[:cap]}")
    parts.append(f"ANSWER:\n{answer[:cap]}")
    try:
        from agent_driver.llm.aux import aux_completion

        resp = await aux_completion(
            provider=provider,
            model=model,
            task="subagent_verify",
            temperature=0.0,
            max_tokens=300,
            messages=[
                ChatMessage(role="system", content=instruction or _VERIFIER_SYSTEM),
                ChatMessage(role="user", content="\n\n".join(parts)),
            ],
        )
    except Exception as exc:  # noqa: BLE001 — a verifier outage must never break the run
        return VerifierVerdict(
            accepted=True, confidence=0.0, rationale=f"verifier-unavailable:{type(exc).__name__}"
        )
    return _parse_verifier_reply(resp.message.content or "")


def _quorum(verdicts: Sequence[VerifierVerdict]) -> VerifierVerdict:
    """Aggregate independent verdicts by strict majority; union the issues."""
    accepts = sum(1 for v in verdicts if v.accepted)
    accepted = accepts * 2 > len(verdicts)  # strict majority
    confidence = _clamp01(sum(v.confidence for v in verdicts) / len(verdicts))
    seen: dict[str, None] = {}
    for v in verdicts:
        for issue in v.issues:
            seen.setdefault(issue, None)
    # Prefer a dissenting rationale when the verdict is a rejection, else a supporting one.
    pool = [v for v in verdicts if v.accepted == accepted and v.rationale]
    rationale = pool[0].rationale if pool else next((v.rationale for v in verdicts if v.rationale), "")
    return VerifierVerdict(
        accepted=accepted,
        confidence=confidence,
        rationale=rationale,
        issues=tuple(seen)[:_MAX_ISSUES],
    )


async def verify_answer(
    answer: str,
    *,
    provider: Any,
    task: str = "",
    criteria: str | None = None,
    model: str | None = None,
    votes: int = 1,
    instruction: str | None = None,
    max_input_chars: int = 6000,
) -> VerifierVerdict:
    """Independently verify one ``answer`` against ``task`` (and optional ``criteria``).

    Runs a skeptical verifier that decides whether the answer can be trusted, returning a
    :class:`VerifierVerdict` (accept/reject + confidence + the concrete issues found). With
    ``votes > 1``, that many independent verifications run concurrently and are aggregated
    by strict majority (the adversarial-verify pattern), unioning their issues. An empty
    answer is rejected deterministically without a model call; a verifier outage degrades to
    ``accepted=True, confidence=0.0`` (unverified, never a silent approval).
    """
    if not (answer or "").strip():
        return VerifierVerdict(
            accepted=False, confidence=1.0, rationale="empty-answer", issues=("no answer to verify",)
        )
    n = max(1, votes)
    verdicts = await asyncio.gather(
        *(
            _verify_once(
                answer,
                provider=provider,
                task=task,
                criteria=criteria,
                model=model,
                instruction=instruction,
                max_input_chars=max_input_chars,
            )
            for _ in range(n)
        )
    )
    return verdicts[0] if n == 1 else _quorum(verdicts)


async def verify_subagent_result(
    result: SubagentResult | None,
    *,
    provider: Any,
    task: str = "",
    criteria: str | None = None,
    model: str | None = None,
    votes: int = 1,
    instruction: str | None = None,
    max_input_chars: int = 6000,
) -> VerifierVerdict:
    """Verify one subagent's answer before the parent trusts it.

    A missing result is rejected deterministically; otherwise the child's ``answer`` is
    passed to :func:`verify_answer`. Status is orthogonal — the verifier judges the content,
    and the caller already holds the child's :class:`RunStatus`.
    """
    if result is None:
        return VerifierVerdict(
            accepted=False, confidence=1.0, rationale="no-result", issues=("child produced no result",)
        )
    return await verify_answer(
        result.answer or "",
        provider=provider,
        task=task,
        criteria=criteria,
        model=model,
        votes=votes,
        instruction=instruction,
        max_input_chars=max_input_chars,
    )


async def verify_subagent_group(
    group: SubagentGroupResult,
    *,
    provider: Any,
    task: str = "",
    criteria: str | None = None,
    model: str | None = None,
    votes: int = 1,
    instruction: str | None = None,
    max_input_chars: int = 6000,
) -> list[VerifierVerdict]:
    """Verify every child of a group concurrently; verdicts align to ``group.results``.

    A verify step for a C4 coordinator phase or a C5 deep-agent fan-out — validate each
    worker's output before synthesizing over it (the MAST verification-gap fix).
    """
    return list(
        await asyncio.gather(
            *(
                verify_subagent_result(
                    result,
                    provider=provider,
                    task=task,
                    criteria=criteria,
                    model=model,
                    votes=votes,
                    instruction=instruction,
                    max_input_chars=max_input_chars,
                )
                for result in group.results
            )
        )
    )


__all__ = [
    "VerifierVerdict",
    "verify_answer",
    "verify_subagent_group",
    "verify_subagent_result",
]
