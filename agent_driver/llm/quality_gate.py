"""Bounded JEV quality and escalation decisions for completed model turns.

The quality gate is deliberately separate from chat completion.  It receives a
small, host-built state envelope and returns one of a fixed set of runtime
actions.  It never selects a provider or an arbitrary model id, and provider
failures fail open to the existing deterministic finalization path.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.decision_contracts import (
    DecisionProvider,
    DecisionQuestion,
    DecisionResponse,
)

QualityGateAction = Literal["accept", "continue_tools", "escalate_strong", "ask_user"]

QUALITY_GATE_SCHEMA = "jev-quality.v1"
RECOVERY_GATE_SCHEMA = "jev-recovery.v1"


def _quality_questions() -> dict[str, DecisionQuestion]:
    return {
        "next_action": DecisionQuestion(
            type="choice",
            instructions=(
                "Choose the single next runtime action for this candidate answer. "
                "Use finalize only when it is sufficient and grounded; use "
                "continue_tools when more allowed tool work is needed; use "
                "escalate_strong when a stronger reasoning pass is needed; use "
                "ask_user only when material ambiguity blocks a reliable answer."
            ),
            criteria={
                "finalize": "The answer is ready to return to the user.",
                "continue_tools": "Allowed tools or evidence are still needed.",
                "escalate_strong": "A stronger model should review or repair the answer.",
                "ask_user": "A material ambiguity requires one user clarification.",
            },
        ),
        "sufficient": DecisionQuestion(
            type="noul",
            instructions="Is the candidate answer sufficient for the user's request?",
        ),
        "needs_tools": DecisionQuestion(
            type="noul",
            instructions="Does reliable completion still require more tool work or evidence?",
        ),
        "grounded": DecisionQuestion(
            type="noul",
            instructions="Is the candidate answer grounded in the available request and evidence?",
        ),
    }


@dataclass(frozen=True, slots=True)
class QualityGateResult:
    """Safe result of one quality-gate call."""

    action: QualityGateAction
    reason: str
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    usage: UsageSummary | None = None


def _noul_probability(response: DecisionResponse, key: str) -> float | None:
    answer = response.answers.get(key)
    if answer is None or answer.type != "noul":
        return None
    return answer.noul


def _confidence(response: DecisionResponse) -> float | None:
    values: list[float] = []
    for answer in response.answers.values():
        if answer.type == "choice" and answer.confidence is not None:
            values.append(answer.confidence)
        elif answer.type == "noul" and answer.noul is not None:
            # ``noul`` exposes P(yes), so distance from 0.5 is the only
            # provider-neutral confidence signal available for that primitive.
            values.append(abs(answer.noul - 0.5) * 2.0)
    return min(values) if values else None


def _answer_metadata(response: DecisionResponse, *, action: str, confidence: float | None) -> dict[str, Any]:
    """Project only labels, probabilities and billing metadata into the trace."""
    answers: dict[str, Any] = {}
    for key, answer in response.answers.items():
        payload: dict[str, Any] = {"type": answer.type}
        if answer.choice is not None:
            payload["choice"] = answer.choice
        if answer.noul is not None:
            payload["noul"] = answer.noul
        if answer.probabilities is not None:
            payload["probabilities"] = dict(answer.probabilities)
        if answer.confidence is not None:
            payload["confidence"] = answer.confidence
        answers[key] = payload
    return {
        "source": "jev",
        "requested_model": response.model,
        "model": response.model,
        "model_version": response.model_version,
        "request_id": response.request_id,
        "provider": response.provider,
        "latency_ms": response.latency_ms,
        "attempts": response.attempts,
        "confidence": confidence,
        "cost_usd": response.cost_usd,
        "input_tokens": response.input_tokens,
        "output_tokens": response.output_tokens,
        "usage_known": response.cost_usd is not None,
        "question_schema": QUALITY_GATE_SCHEMA,
        "action": action,
        "answers": answers,
    }


def _recovery_questions() -> dict[str, DecisionQuestion]:
    return {
        "repair_action": DecisionQuestion(
            type="choice",
            instructions=(
                "Choose one bounded recovery action for a malformed tool call or "
                "tool/provider failure. Retry only when the current policy permits "
                "it; never bypass a denied or irreversible operation."
            ),
            criteria={
                "retry_tool": "Retry an allowed tool after repairing its arguments.",
                "repair_prompt": "Ask the generation model to repair its tool-call format.",
                "ask_user": "Ask the user for clarification before another attempt.",
                "accept": "Keep the current answer and report the limitation.",
            },
        ),
        "retryable": DecisionQuestion(
            type="noul",
            instructions="Is one bounded recovery attempt likely to resolve the failure?",
        ),
    }


class JevQualityGate:
    """Evaluate a candidate answer and choose a bounded control action.

    The gate is advisory.  The runtime owns the escalation budget, tool policy,
    and retry limits; this class only maps a validated decision response to a
    known action.
    """

    def __init__(
        self,
        *,
        decision_provider: DecisionProvider,
        model: str = "typesafe/jev-1.13",
        strong_role: str = "strong",
        min_confidence: float = 0.55,
        negative_threshold: float = 0.60,
        max_input_chars: int = 4000,
    ) -> None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("decision model must be a non-empty string")
        if not isinstance(strong_role, str) or not strong_role.strip():
            raise ValueError("strong_role must be a non-empty string")
        if not 0.0 <= min_confidence <= 1.0:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0.5 <= negative_threshold <= 1.0:
            raise ValueError("negative_threshold must be between 0.5 and 1")
        if max_input_chars <= 0:
            raise ValueError("max_input_chars must be positive")
        self._decision_provider = decision_provider
        self.model = model
        self.strong_role = strong_role
        self.min_confidence = min_confidence
        self.negative_threshold = negative_threshold
        self.max_input_chars = max_input_chars
        self._last_decision_metadata: dict[str, Any] = {}
        self._last_decision_usage: UsageSummary | None = None

    @property
    def last_decision_metadata(self) -> dict[str, Any]:
        return dict(self._last_decision_metadata)

    @property
    def last_decision_usage(self) -> UsageSummary | None:
        if self._last_decision_usage is None:
            return None
        return self._last_decision_usage.model_copy(deep=True)

    def _fallback(self, reason: str) -> QualityGateResult:
        self._last_decision_usage = None
        self._last_decision_metadata = {
            "source": "fallback",
            "action": "accept",
            "fallback_reason": reason,
            "question_schema": QUALITY_GATE_SCHEMA,
        }
        return QualityGateResult(
            action="accept",
            reason=reason,
            metadata=dict(self._last_decision_metadata),
        )

    async def evaluate(self, *, state: Mapping[str, Any]) -> QualityGateResult:
        """Evaluate one bounded candidate envelope.

        The envelope is copied and clipped before reaching the provider.  A
        malformed response, timeout, circuit-open state, or transport error
        accepts the candidate and leaves existing deterministic runtime guards
        authoritative.
        """
        self._last_decision_metadata = {}
        self._last_decision_usage = None
        bounded = dict(state)
        for key in ("request", "candidate_answer"):
            value = bounded.get(key)
            if isinstance(value, str):
                bounded[key] = value[: self.max_input_chars]
        try:
            questions = _quality_questions()
            response = await self._decision_provider.decide(
                state=bounded,
                questions=questions,
                model=self.model,
            )
            response.validate_questions(questions)
            usage = response.usage(task="quality_gate")
        except Exception as exc:  # noqa: BLE001 — a gate must never break a run
            return self._fallback(type(exc).__name__)

        action_answer = response.answers.get("next_action")
        action = action_answer.choice if action_answer is not None else None
        confidence = _confidence(response)
        sufficient = _noul_probability(response, "sufficient")
        needs_tools = _noul_probability(response, "needs_tools")
        grounded = _noul_probability(response, "grounded")

        if action == "ask_user":
            selected: QualityGateAction = "ask_user"
            reason = "jev_material_ambiguity"
        elif action == "continue_tools" or (
            needs_tools is not None and needs_tools >= self.negative_threshold
        ):
            selected = "continue_tools"
            reason = "jev_more_tools_or_evidence"
        elif action == "escalate_strong":
            selected = "escalate_strong"
            reason = "jev_strong_review"
        elif (
            action != "finalize"
            or sufficient is None
            or sufficient < self.negative_threshold
            or grounded is None
            or grounded < self.negative_threshold
            or confidence is None
            or confidence < self.min_confidence
        ):
            selected = "escalate_strong"
            reason = "jev_low_confidence_or_insufficient"
        else:
            selected = "accept"
            reason = "jev_answer_sufficient"

        metadata = _answer_metadata(response, action=selected, confidence=confidence)
        metadata.update(
            {
                "reason": reason,
                "sufficient_probability": sufficient,
                "needs_tools_probability": needs_tools,
                "grounded_probability": grounded,
            }
        )
        self._last_decision_metadata = metadata
        self._last_decision_usage = usage
        return QualityGateResult(
            action=selected,
            reason=reason,
            confidence=confidence,
            metadata=dict(metadata),
            usage=usage,
        )

    async def classify_recovery(self, *, state: Mapping[str, Any]) -> QualityGateResult:
        """Classify one bounded tool/provider recovery hint.

        The runtime still owns retry counters and static policy. A ``retry_tool``
        result only causes a new model turn to reconsider an allowed call; it
        never dispatches a tool or changes a deny decision itself.
        """
        self._last_decision_metadata = {}
        self._last_decision_usage = None
        bounded = dict(state)
        for key in ("request", "candidate_answer"):
            value = bounded.get(key)
            if isinstance(value, str):
                bounded[key] = value[: self.max_input_chars]
        try:
            questions = _recovery_questions()
            response = await self._decision_provider.decide(
                state=bounded,
                questions=questions,
                model=self.model,
            )
            response.validate_questions(questions)
            usage = response.usage(task="quality_gate_recovery")
        except Exception as exc:  # noqa: BLE001 — recovery must remain fail-open
            self._last_decision_metadata = {
                "source": "fallback",
                "action": "accept",
                "fallback_reason": type(exc).__name__,
                "question_schema": RECOVERY_GATE_SCHEMA,
            }
            return QualityGateResult(
                action="accept",
                reason=type(exc).__name__,
                metadata=dict(self._last_decision_metadata),
            )

        action_answer = response.answers.get("repair_action")
        requested = action_answer.choice if action_answer is not None else None
        retryable = _noul_probability(response, "retryable")
        confidence = _confidence(response)
        if requested not in {"retry_tool", "repair_prompt", "ask_user"}:
            selected = "accept"
            reason = "jev_recovery_accept"
        elif retryable is None or retryable < self.negative_threshold:
            selected = "accept"
            reason = "jev_recovery_not_retryable"
        elif confidence is None or confidence < self.min_confidence:
            selected = "accept"
            reason = "jev_recovery_low_confidence"
        else:
            selected = requested
            reason = f"jev_recovery_{requested}"
        metadata = _answer_metadata(response, action=selected, confidence=confidence)
        metadata.update(
            {
                "question_schema": RECOVERY_GATE_SCHEMA,
                "reason": reason,
                "retryable_probability": retryable,
            }
        )
        self._last_decision_metadata = metadata
        self._last_decision_usage = usage
        return QualityGateResult(
            action=selected,
            reason=reason,
            confidence=confidence,
            metadata=dict(metadata),
            usage=usage,
        )


__all__ = [
    "JevQualityGate",
    "QUALITY_GATE_SCHEMA",
    "RECOVERY_GATE_SCHEMA",
    "QualityGateAction",
    "QualityGateResult",
]
