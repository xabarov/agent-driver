"""Bounded JEV gate for safe long-term memory writes.

The gate sits between fact extraction and the memory store. It classifies each
candidate as durable, session-only, obsolete, sensitive, or uncertain, then
checks durable candidates against the existing memory before allowing a write.
Only typed decisions cross the boundary; receipts contain hashes and counts,
never candidate text or existing-memory content.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.decision_contracts import (
    DecisionProvider,
    DecisionQuestion,
    DecisionResponse,
)
from agent_driver.llm.rollout import (
    jev_task_category,
    observe_jev_gate,
    resolve_jev_mode,
)
from agent_driver.memory.provider import MemoryRecord, MemoryTurn

MEMORY_DURABILITY_SCHEMA = "jev-memory-durability.v1"
MemoryDisposition = Literal[
    "durable", "session_only", "obsolete", "sensitive", "uncertain"
]

_CATEGORY_CRITERIA: dict[str, str] = {
    "durable": (
        "Stable user preference, identity, standing decision, recurring interest, "
        "persistent environment fact, or an explicit request to remember it."
    ),
    "session_only": (
        "Temporary task state, current plan, one-off request, progress detail, "
        "or information useful only in this conversation."
    ),
    "obsolete": "Superseded, retracted, completed, or no longer valid information.",
    "sensitive": (
        "A secret, credential, access code, private sensitive value, direct "
        "identifier, or token."
    ),
    "uncertain": "The candidate cannot be classified safely from the available evidence.",
}

_SENSITIVE_RE = re.compile(
    r"(?:api[_ -]?key|access[_ -]?token|password|passwd|secret|credential|"
    r"bearer|authorization)\s*[:=]\s*\S+|"
    r"\b(?:sk|ghp|github_pat|xox[baprs])-[-_A-Za-z0-9]{12,}\b|"
    # Direct identifiers are screened before sending candidate state to JEV;
    # ordinary names and stable preferences remain eligible for durable memory.
    r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|"
    r"\b\d{3}-\d{2}-\d{4}\b|"
    r"\b(?:\+?\d[\d(). -]{7,}\d)\b|"
    r"\b(?:\d[ -]?){13,19}\b",
    flags=re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class MemoryGateResult:
    """Accepted candidates, held candidates, and a raw-free decision receipt."""

    accepted: tuple[dict[str, str], ...]
    session_only: tuple[dict[str, str], ...]
    held: tuple[dict[str, str], ...]
    receipt: dict[str, Any]
    usage: UsageSummary | None = None


def _candidate_hash(text: str, slot: str | None) -> str:
    payload = f"{slot or ''}\x00{text}".encode("utf-8")
    return f"fact_{hashlib.sha256(payload).hexdigest()[:20]}"


def _fact_hash(record: MemoryRecord) -> str:
    fact_id = record.metadata.get("fact_id")
    if isinstance(fact_id, str) and fact_id.strip():
        return fact_id.strip()
    return _candidate_hash(record.text, record.metadata.get("slot"))


def _is_sensitive(text: str) -> bool:
    return bool(_SENSITIVE_RE.search(text))


def _receipt_candidate_id(item: Mapping[str, str]) -> str:
    """Hash a candidate for a receipt without hashing sensitive raw text."""
    text = (
        "[redacted sensitive candidate]"
        if _is_sensitive(item["text"])
        else item["text"]
    )
    slot = item.get("slot")
    if isinstance(slot, str) and _is_sensitive(slot):
        slot = "[redacted sensitive slot]"
    return _candidate_hash(text, slot)


def _safe_slot(slot: object) -> str | None:
    if not isinstance(slot, str) or not slot.strip():
        return None
    return "[redacted sensitive slot]" if _is_sensitive(slot) else slot.strip()[:64]


def _questions(count: int) -> dict[str, DecisionQuestion]:
    questions: dict[str, DecisionQuestion] = {}
    for index in range(count):
        prefix = f"candidate_{index}"
        questions[f"{prefix}_category"] = DecisionQuestion(
            type="choice",
            instructions=(
                f"Classify only state.candidates[{index}]. Choose the single category "
                "that best describes the candidate for long-term memory. Do not infer "
                "facts that are not present."
            ),
            criteria=dict(_CATEGORY_CRITERIA),
        )
        questions[f"{prefix}_contradiction"] = DecisionQuestion(
            type="noul",
            instructions=(
                f"Evaluate only state.candidates[{index}] against state.existing_memory. "
                "Does the candidate contradict a current existing memory fact?"
            ),
            criteria={
                "true": "It conflicts with an existing fact and the conflict is unresolved.",
                "false": "It does not conflict, or it clarifies the same fact without conflict.",
            },
        )
    return questions


def _answer(response: DecisionResponse, key: str, kind: str) -> Any:
    answer = response.answers.get(key)
    if answer is None or answer.type != kind:
        return None
    return answer


def _bounded_text(text: str, max_chars: int) -> str:
    return text.strip()[:max_chars]


class MemoryDurabilityGate:
    """Classify and contradiction-check bounded memory candidates with JEV."""

    def __init__(
        self,
        *,
        decision_provider: DecisionProvider,
        model: str = "typesafe/jev-1.13",
        min_confidence: float = 0.60,
        contradiction_probability: float = 0.50,
        max_candidates: int = 8,
        max_existing_records: int = 20,
        max_candidate_chars: int = 1200,
        max_existing_chars: int = 800,
        rollout_policy: object | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("decision model must be a non-empty string")
        if not 0 <= min_confidence <= 1:
            raise ValueError("min_confidence must be between 0 and 1")
        if not 0 <= contradiction_probability <= 1:
            raise ValueError("contradiction_probability must be between 0 and 1")
        if max_candidates < 1 or max_existing_records < 0:
            raise ValueError("memory gate bounds are invalid")
        if max_candidate_chars < 64 or max_existing_chars < 64:
            raise ValueError("memory gate text bounds are too small")
        self._decision_provider = decision_provider
        self.model = model
        self.min_confidence = min_confidence
        self.contradiction_probability = contradiction_probability
        self.max_candidates = max_candidates
        self.max_existing_records = max_existing_records
        self.max_candidate_chars = max_candidate_chars
        self.max_existing_chars = max_existing_chars
        self.rollout_policy = rollout_policy

    def _receipt_base(self, *, count: int) -> dict[str, Any]:
        return {
            "schema": MEMORY_DURABILITY_SCHEMA,
            "candidate_count": count,
            "accepted_count": 0,
            "session_only_count": 0,
            "held_count": 0,
            "rejected_count": 0,
            "decisions": [],
        }

    def _fallback(
        self,
        candidates: Sequence[dict[str, str]],
        *,
        reason: str,
    ) -> MemoryGateResult:
        held = tuple(candidates[: self.max_candidates])
        receipt = self._receipt_base(count=len(held))
        receipt.update(
            {
                "source": "fallback",
                "fallback_reason": reason,
                "held_count": len(held),
                "decisions": [
                    {
                        "candidate_id": _receipt_candidate_id(item),
                        "category": "uncertain",
                        "action": "hold_uncertain",
                    }
                    for item in held
                ],
            }
        )
        return MemoryGateResult(
            accepted=(), session_only=(), held=held, receipt=receipt
        )

    async def classify(
        self,
        *,
        turn: MemoryTurn,
        candidates: Sequence[Mapping[str, str]],
        existing_records: Sequence[MemoryRecord],
    ) -> MemoryGateResult:
        """Return only candidates safe to persist as durable facts."""
        normalized: list[dict[str, str]] = []
        for item in candidates[: self.max_candidates]:
            text = item.get("text")
            if not isinstance(text, str) or not text.strip():
                continue
            slot = item.get("slot")
            normalized.append(
                {
                    "text": _bounded_text(text, self.max_candidate_chars),
                    **(
                        {"slot": slot.strip()[:64]}
                        if isinstance(slot, str) and slot.strip()
                        else {}
                    ),
                }
            )
        if not normalized:
            receipt = self._receipt_base(count=0)
            receipt.update({"source": "deterministic", "reason": "no_candidates"})
            return MemoryGateResult(accepted=(), session_only=(), held=(), receipt=receipt)
        task = jev_task_category(turn.metadata)
        rollout_mode = resolve_jev_mode(self.rollout_policy, "memory", task=task)
        if rollout_mode == "off":
            held = tuple(normalized)
            receipt = self._receipt_base(count=len(held))
            receipt.update(
                {
                    "source": "rollout_disabled",
                    "rollout_mode": "off",
                    "task": task,
                    "held_count": len(held),
                    "decisions": [
                        {
                            "candidate_id": _receipt_candidate_id(item),
                            "category": "uncertain",
                            "action": "hold_rollout_disabled",
                        }
                        for item in held
                    ],
                }
            )
            return MemoryGateResult(
                accepted=(), session_only=(), held=held, receipt=receipt
            )

        # Sensitive candidates are rejected before the network call. Their text
        # is replaced in the bounded state so a credential is never sent to JEV.
        preclassified: dict[int, str] = {
            index: "sensitive"
            for index, item in enumerate(normalized)
            if _is_sensitive(item["text"])
            or _is_sensitive(item.get("slot", ""))
        }
        state = {
            "schema": MEMORY_DURABILITY_SCHEMA,
            "turn": {
                "run_id": turn.run_id,
                "session_id": turn.session_id,
                "user_chars": len(turn.user_text or ""),
                "assistant_chars": len(turn.assistant_text or ""),
            },
            "candidates": [
                {
                    "index": index,
                    "text": "[redacted sensitive candidate]"
                    if index in preclassified
                    else item["text"],
                    **(
                        {"slot": _safe_slot(item.get("slot"))}
                        if _safe_slot(item.get("slot")) is not None
                        else {}
                    ),
                }
                for index, item in enumerate(normalized)
            ],
            "existing_memory": [
                {
                    "id": _fact_hash(record),
                    "slot": _safe_slot(record.metadata.get("slot")),
                    "text": "[redacted sensitive memory]"
                    if _is_sensitive(record.text)
                    else _bounded_text(record.text, self.max_existing_chars),
                }
                for record in list(existing_records)[: self.max_existing_records]
            ],
        }
        questions = _questions(len(normalized))
        try:
            response = await self._decision_provider.decide(
                state=state, questions=questions, model=self.model
            )
            response.validate_questions(questions)
        except Exception as exc:  # noqa: BLE001 — memory must fail closed
            fallback = self._fallback(normalized, reason=type(exc).__name__)
            fallback.receipt.update(
                {
                    "candidate_count": len(normalized),
                    "model": getattr(exc, "model", None),
                    "rollout_mode": rollout_mode,
                    "task": task,
                }
            )
            observe_jev_gate(
                self.rollout_policy,
                gate="memory",
                task=task,
                fallback=True,
                latency_ms=None,
            )
            return fallback

        accepted: list[dict[str, str]] = []
        session_only: list[dict[str, str]] = []
        held: list[dict[str, str]] = []
        decisions: list[dict[str, Any]] = []
        for index, item in enumerate(normalized):
            category = preclassified.get(index)
            category_confidence: float | None = None
            contradiction: float | None = None
            if category is None:
                category_answer = _answer(
                    response, f"candidate_{index}_category", "choice"
                )
                category = (
                    str(category_answer.choice)
                    if category_answer is not None and category_answer.choice
                    else "uncertain"
                )
                category_confidence = (
                    category_answer.confidence if category_answer is not None else None
                )
            contradiction_answer = _answer(
                response, f"candidate_{index}_contradiction", "noul"
            )
            contradiction = (
                contradiction_answer.noul if contradiction_answer is not None else None
            )
            candidate_id = _receipt_candidate_id(item)
            if category in {"sensitive", "obsolete"}:
                action = f"reject_{category}"
                target = None
            elif category == "session_only":
                action = "session_only"
                target = session_only
            elif (
                category != "durable"
                or category_confidence is None
                or category_confidence < self.min_confidence
            ):
                action = "hold_uncertain"
                target = held
            elif (
                contradiction is None
                or contradiction >= self.contradiction_probability
            ):
                action = "hold_contradiction"
                target = held
            else:
                action = "accept_durable"
                target = accepted
            if target is not None:
                target.append(item)
            decisions.append(
                {
                    "candidate_id": candidate_id,
                    "category": category,
                    "confidence": category_confidence,
                    "contradiction_probability": contradiction,
                    "action": action,
                }
            )

        receipt = self._receipt_base(count=len(normalized))
        receipt.update(
            {
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
                "min_confidence": self.min_confidence,
                "contradiction_probability": self.contradiction_probability,
                "accepted_count": len(accepted) if rollout_mode == "active" else 0,
                "session_only_count": len(session_only),
                "held_count": len(held),
                "rejected_count": sum(
                    str(decision["action"]).startswith("reject_")
                    for decision in decisions
                ),
                "decisions": decisions,
                "rollout_mode": rollout_mode,
                "task": task,
                "applied": rollout_mode == "active",
                "proposed_accepted_count": len(accepted),
            }
        )
        observe_jev_gate(
            self.rollout_policy,
            gate="memory",
            task=task,
            fallback=False,
            cost_usd=response.cost_usd,
            latency_ms=response.latency_ms,
        )
        return MemoryGateResult(
            accepted=tuple(accepted) if rollout_mode == "active" else (),
            session_only=tuple(session_only),
            held=tuple(held),
            receipt=receipt,
            usage=response.usage(task="memory_durability_gate"),
        )


__all__ = [
    "MEMORY_DURABILITY_SCHEMA",
    "MemoryDisposition",
    "MemoryDurabilityGate",
    "MemoryGateResult",
]
