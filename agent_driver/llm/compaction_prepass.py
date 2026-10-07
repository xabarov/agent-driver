"""Bounded JEV pre-pass for context compaction.

The pre-pass is deliberately a small decision task rather than a summary
request.  It classifies already segmented transcript units into a retention
class; the existing compactor remains responsible for producing the summary.
Provider failures and low confidence keep the original view intact.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.decision_contracts import (
    DecisionProvider,
    DecisionQuestion,
    DecisionResponse,
)

COMPACTION_PREPASS_SCHEMA = "jev-compaction.v1"
RetentionClass = Literal["protected", "retain", "summarize", "archive"]


@dataclass(frozen=True, slots=True)
class CompactionUnit:
    """A bounded semantic/message unit presented to JEV."""

    unit_id: str
    message_indexes: tuple[int, ...]
    role: str
    text: str
    protected: bool = False


@dataclass(frozen=True, slots=True)
class CompactionUnitDecision:
    """Raw-free retention decision for one unit."""

    unit_id: str
    message_indexes: tuple[int, ...]
    retention: RetentionClass
    relevance: float | None = None
    new_constraint: float | None = None
    contradiction: float | None = None
    safe_to_remove: float | None = None
    confidence: float | None = None
    protected: bool = False


@dataclass(frozen=True, slots=True)
class CompactionPrepassResult:
    """Result consumed by the runtime compaction stage."""

    decisions: tuple[CompactionUnitDecision, ...]
    archive_indexes: frozenset[int]
    receipt: dict[str, Any]
    usage: UsageSummary | None = None


def _questions(units: Sequence[CompactionUnit]) -> dict[str, DecisionQuestion]:
    questions: dict[str, DecisionQuestion] = {}
    for index, _unit in enumerate(units):
        prefix = f"unit_{index}"
        questions[f"{prefix}_relevance"] = DecisionQuestion(
            type="score",
            instructions=(
                f"Score only transcript state.units[{index}] for relevance to the active_request. "
                "Use the ordered criteria exactly. Do not score the other units, and do not "
                "invent a new request."
            ),
            criteria=[
                "0: unrelated historical detail with no current bearing",
                "1: weak context that can be represented by a summary",
                "2: useful context for continuity or evidence",
                "3: directly needed for the current request or next action",
            ],
        )
        questions[f"{prefix}_new_constraint"] = DecisionQuestion(
            type="noul",
            instructions=(
                f"Evaluate only state.units[{index}]. Is this unit itself a user directive, "
                "requirement, final decision, exact identifier, open goal, or unresolved error "
                "that must survive compaction?"
            ),
            criteria={
                "true": "The unit contains such durable information.",
                "false": "The unit contains only completed, replaceable, or generic background.",
            },
        )
        questions[f"{prefix}_contradiction"] = DecisionQuestion(
            type="noul",
            instructions=(
                f"Evaluate only state.units[{index}]. Does it contain an unresolved "
                "contradiction, correction, or superseded value that the compactor must see "
                "to avoid a wrong answer?"
            ),
            criteria={
                "true": "A contradiction or correction remains relevant and unresolved.",
                "false": "There is no relevant contradiction, or the unit is already settled.",
            },
        )
        questions[f"{prefix}_safe_to_remove"] = DecisionQuestion(
            type="noul",
            instructions=(
                f"Evaluate only state.units[{index}]. Is it safe to remove this unit from the "
                "compactor input while preserving the answer to active_request?"
            ),
            criteria={
                "true": (
                    "Removing it cannot lose a directive, exact identifier, open goal, "
                    "unresolved error, correction, or fact needed for the active request."
                ),
                "false": "Removing it could lose any of those facts or make the answer unreliable.",
            },
        )
    return questions


def _score(response: DecisionResponse, key: str) -> float | None:
    answer = response.answers.get(key)
    if answer is None or answer.type != "score":
        return None
    return answer.score


def _noul(response: DecisionResponse, key: str) -> float | None:
    answer = response.answers.get(key)
    if answer is None or answer.type != "noul":
        return None
    return answer.noul


def _confidence(response: DecisionResponse, keys: Sequence[str]) -> float:
    """Return confidence for the scored relevance question only.

    Noul answers are probabilities of yes, not confidence values. Treating
    distance from 0.5 as confidence makes every bundled question a veto and
    causes an unrelated question to force a global fallback.
    """
    for key in keys:
        answer = response.answers[key]
        if answer.type == "score" and answer.confidence is not None:
            return answer.confidence
    return 0.0


def _unit_hash(unit: CompactionUnit) -> str:
    return hashlib.sha256(unit.text.encode("utf-8")).hexdigest()


def _bounded_state(state: Mapping[str, Any], max_chars: int) -> dict[str, Any]:
    """Keep the active envelope below the configured wire-size budget."""
    candidate = dict(state)
    try:
        encoded = json.dumps(candidate, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        encoded = str(candidate)
    if len(encoded) <= max_chars:
        return candidate
    # Preserve the envelope shape for simple fields, then fall back to a
    # clipped serialized excerpt if nested host state is unusually large.
    clipped: dict[str, Any] = {}
    per_field = max(64, max_chars // max(1, len(candidate)))
    for key, value in candidate.items():
        if isinstance(value, str):
            clipped[key] = value[:per_field]
        else:
            clipped[key] = str(value)[:per_field]
    try:
        if len(json.dumps(clipped, ensure_ascii=False, default=str)) <= max_chars:
            return clipped
    except (TypeError, ValueError):
        pass
    return {"truncated": True, "excerpt": encoded[:max_chars]}


def _fallback(
    units: Sequence[CompactionUnit],
    *,
    reason: str,
    confidence: float | None = None,
    response: DecisionResponse | None = None,
) -> CompactionPrepassResult:
    decisions = tuple(
        CompactionUnitDecision(
            unit_id=unit.unit_id,
            message_indexes=unit.message_indexes,
            retention="protected" if unit.protected else "summarize",
            confidence=confidence,
            protected=unit.protected,
        )
        for unit in units
    )
    receipt: dict[str, Any] = {
        "schema": COMPACTION_PREPASS_SCHEMA,
        "source": "fallback",
        "fallback_reason": reason,
        "confidence": confidence,
        "unit_count": len(units),
        "archived_unit_sha256": [],
        "decisions": [
            {
                "unit_id": item.unit_id,
                "message_indexes": list(item.message_indexes),
                "retention": item.retention,
                "protected": item.protected,
            }
            for item in decisions
        ],
    }
    usage = None
    if response is not None:
        receipt.update(
            {
                "model": response.model,
                "model_version": response.model_version,
                "request_id": response.request_id,
                "provider": response.provider,
                "latency_ms": response.latency_ms,
                "attempts": response.attempts,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "cost_usd": response.cost_usd,
            }
        )
        usage = response.usage(task="compaction_prepass")
    return CompactionPrepassResult(
        decisions=decisions,
        archive_indexes=frozenset(),
        receipt=receipt,
        usage=usage,
    )


class JevCompactionPrepass:
    """Classify bounded transcript units with one batched JEV request."""

    def __init__(
        self,
        *,
        decision_provider: DecisionProvider,
        model: str = "typesafe/jev-1.13",
        min_confidence: float = 0.55,
        archive_relevance_max: float = 1.5,
        archive_probability: float = 0.70,
        retain_relevance_min: float = 2.0,
        retain_probability: float = 0.55,
        max_units: int = 12,
        max_unit_chars: int = 1600,
        max_state_chars: int = 2400,
    ) -> None:
        if not model.strip():
            raise ValueError("decision model must be a non-empty string")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0 <= archive_relevance_max <= 3:
            raise ValueError("archive_relevance_max must be between 0 and 3")
        if not 0.5 <= archive_probability <= 1:
            raise ValueError("archive_probability must be between 0.5 and 1")
        if not 0 <= retain_relevance_min <= 3:
            raise ValueError("retain_relevance_min must be between 0 and 3")
        if not 0.5 <= retain_probability <= 1:
            raise ValueError("retain_probability must be between 0.5 and 1")
        if max_units < 1 or max_unit_chars < 64 or max_state_chars < 128:
            raise ValueError("pre-pass bounds are too small")
        self._decision_provider = decision_provider
        self.model = model
        self.min_confidence = min_confidence
        self.archive_relevance_max = archive_relevance_max
        self.archive_probability = archive_probability
        self.retain_relevance_min = retain_relevance_min
        self.retain_probability = retain_probability
        self.max_units = max_units
        self.max_unit_chars = max_unit_chars
        self.max_state_chars = max_state_chars

    async def classify(
        self,
        *,
        active_state: Mapping[str, Any],
        units: Sequence[CompactionUnit],
    ) -> CompactionPrepassResult:
        """Return retention classes; never raise for a provider decision failure."""
        bounded_units = tuple(units[: self.max_units])
        if not bounded_units:
            return _fallback(bounded_units, reason="no_units")
        # Retain deterministic protection for units outside the bounded JEV batch.
        if len(units) > self.max_units:
            bounded_units = tuple(units[: self.max_units])
        questions = _questions(bounded_units)
        unprotected = [unit for unit in bounded_units if not unit.protected]
        if not unprotected:
            decisions = tuple(
                CompactionUnitDecision(
                    unit_id=unit.unit_id,
                    message_indexes=unit.message_indexes,
                    retention="protected",
                    protected=True,
                )
                for unit in bounded_units
            )
            return CompactionPrepassResult(
                decisions=decisions,
                archive_indexes=frozenset(),
                receipt={
                    "schema": COMPACTION_PREPASS_SCHEMA,
                    "source": "deterministic",
                    "fallback_reason": "all_units_protected",
                    "unit_count": len(bounded_units),
                    "archived_unit_sha256": [],
                    "decisions": [
                        {"unit_id": d.unit_id, "retention": d.retention, "protected": True}
                        for d in decisions
                    ],
                },
            )
        bounded_active_state = _bounded_state(active_state, self.max_state_chars)
        state = {
            "schema": COMPACTION_PREPASS_SCHEMA,
            "active_request": str(bounded_active_state.get("current_request", ""))[:1600],
            "active_state": bounded_active_state,
            "units": [
                {
                    "id": unit.unit_id,
                    "role": unit.role,
                    "protected": unit.protected,
                    "chars": len(unit.text),
                    "text": unit.text[: self.max_unit_chars],
                }
                for unit in bounded_units
            ],
        }
        try:
            response = await self._decision_provider.decide(
                state=state,
                questions=questions,
                model=self.model,
            )
            response.validate_questions(questions)
        except Exception as exc:  # noqa: BLE001 — compaction must fail open
            return _fallback(bounded_units, reason=type(exc).__name__)

        decisions: list[CompactionUnitDecision] = []
        archive_indexes: set[int] = set()
        all_confidences: list[float] = []
        for index, unit in enumerate(bounded_units):
            prefix = f"unit_{index}"
            keys = [
                f"{prefix}_relevance",
                f"{prefix}_new_constraint",
                f"{prefix}_contradiction",
                f"{prefix}_safe_to_remove",
            ]
            confidence = _confidence(response, keys)
            all_confidences.append(confidence)
            relevance = _score(response, keys[0])
            new_constraint = _noul(response, keys[1])
            contradiction = _noul(response, keys[2])
            safe_to_remove = _noul(response, keys[3])
            if unit.protected:
                retention: RetentionClass = "protected"
            elif confidence < self.min_confidence:
                # An uncertain unit stays verbatim. Summarization can lose an
                # exact value even when the unit is probably relevant; the
                # compactor can still summarize other, high-confidence units.
                retention = "retain"
            elif (
                relevance is not None
                and relevance <= self.archive_relevance_max
                and safe_to_remove is not None
                and safe_to_remove >= self.archive_probability
                and (new_constraint or 0.0) < 0.5
                and (contradiction or 0.0) < 0.5
            ):
                retention = "archive"
                archive_indexes.update(unit.message_indexes)
            elif (
                (relevance or 0.0) >= self.retain_relevance_min
                or (new_constraint or 0.0) >= self.retain_probability
                or (contradiction or 0.0) >= self.retain_probability
            ):
                retention = "retain"
            else:
                retention = "summarize"
            decisions.append(
                CompactionUnitDecision(
                    unit_id=unit.unit_id,
                    message_indexes=unit.message_indexes,
                    retention=retention,
                    relevance=relevance,
                    new_constraint=new_constraint,
                    contradiction=contradiction,
                    safe_to_remove=safe_to_remove,
                    confidence=confidence,
                    protected=unit.protected,
                )
            )
        confidence = min(all_confidences) if all_confidences else None
        # Keep the fail-open behavior when the whole batch is uncertain, while
        # allowing clearly scored units to archive when one neighboring unit is
        # ambiguous. Noul probabilities do not participate in this gate.
        if all(
            item < self.min_confidence for item in all_confidences
        ):
            return _fallback(
                bounded_units,
                reason="low_confidence",
                confidence=confidence,
                response=response,
            )
        by_id = {unit.unit_id: unit for unit in bounded_units}
        receipt_decisions = [
            {
                "unit_id": decision.unit_id,
                "message_indexes": list(decision.message_indexes),
                "retention": decision.retention,
                "protected": decision.protected,
                "relevance": decision.relevance,
                "new_constraint": decision.new_constraint,
                "contradiction": decision.contradiction,
                "safe_to_remove": decision.safe_to_remove,
                "confidence": decision.confidence,
            }
            for decision in decisions
        ]
        receipt = {
            "schema": COMPACTION_PREPASS_SCHEMA,
            "source": "jev",
            "model": response.model,
            "model_version": response.model_version,
            "request_id": response.request_id,
            "provider": response.provider,
            "latency_ms": response.latency_ms,
            "attempts": response.attempts,
            "input_tokens": response.input_tokens,
            "output_tokens": response.output_tokens,
            "cost_usd": response.cost_usd,
            "confidence": confidence,
            "min_confidence": self.min_confidence,
            "archive_relevance_max": self.archive_relevance_max,
            "archive_probability": self.archive_probability,
            "retain_relevance_min": self.retain_relevance_min,
            "retain_probability": self.retain_probability,
            "unit_count": len(bounded_units),
            "archived_unit_sha256": sorted(
                _unit_hash(by_id[decision.unit_id])
                for decision in decisions
                if decision.retention == "archive"
            ),
            "decisions": receipt_decisions,
        }
        return CompactionPrepassResult(
            decisions=tuple(decisions),
            archive_indexes=frozenset(archive_indexes),
            receipt=receipt,
            usage=response.usage(task="compaction_prepass"),
        )


__all__ = [
    "COMPACTION_PREPASS_SCHEMA",
    "CompactionPrepassResult",
    "CompactionUnit",
    "CompactionUnitDecision",
    "JevCompactionPrepass",
    "RetentionClass",
]
