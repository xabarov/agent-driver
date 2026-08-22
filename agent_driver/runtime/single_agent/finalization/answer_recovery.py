"""Finalize-time recovery of a degenerate terminal answer (epic 015, Phase C).

When a ReAct loop over-iterates on a pure-text run — the model produces a complete answer, then
re-answers without any tool call and finalizes a degenerate «task already done / see previous answer»
stub (or an empty turn) — the last assistant turn becomes the run answer and the real answer is lost.

This module recovers the substantive answer the model already produced, from the run's
``assistant_message_completed`` event log. It is a raw-free, language-agnostic safety net:

* Gated on ``tool_call_count == 0`` — a tool-informed run's terminal answer is trusted and never
  overridden (the last turn is a legitimate post-tool synthesis, not a no-progress restatement).
* Only triggers when the terminal answer is empty, or a short restatement much shorter than an
  earlier substantive turn of the same run.

The detector is deterministic and pure so it can be unit-tested without a live loop; the streaming
over-iteration that produces the degenerate finalize is exercised separately (epic 015 Phase A/D).
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

# A turn long enough to be a real answer rather than a hedge/stub.
SUBSTANTIVE_ANSWER_CHARS = 400
# The earlier substantive turn must dwarf the terminal by this factor to count as a discarded real
# answer that a no-progress re-answer replaced.
MIN_SUBSTANTIVE_RATIO = 4

# CJK / Kana / Hangul — used to detect a wrong-language degenerate answer.
_CJK_RE = re.compile(r"[぀-ヿ㐀-䶿一-鿿가-힯豈-﫿]")
# Canned «I'm just an AI / I haven't learned to answer» non-answers some models emit instead of using
# the retrieved context (notably deepseek's Chinese fallback). Language-mixed, matched case-insensitively.
_CANNED_REFUSAL_MARKERS = (
    "作为一个人工智能",
    "我还没学习",
    "人工智能语言模型",
    "as an ai language model",
    "i haven't learned how to answer",
    "i have not learned how to answer",
    "i'm just an ai",
    "i am just an ai",
)

# Long provider-corruption responses can be non-empty and use the requested
# language while repeating one fragment hundreds of times. Bound the detector
# well above ordinary short answers and require both one dominant token and
# very low token diversity so normal prose, code, and tables remain untouched.
_REPETITION_MIN_TOKENS = 80
_REPETITION_DOMINANT_RATIO = 0.55
_REPETITION_MAX_UNIQUE_RATIO = 0.20


def _cjk_ratio(text: str) -> float:
    letters = [ch for ch in text if ch.isalpha()]
    if not letters:
        return 0.0
    return sum(1 for ch in letters if _CJK_RE.match(ch)) / len(letters)


def final_content_unusable(content: str | None, input_text: str = "") -> bool:
    """THE single «did this final-answer strategy fail?» predicate (epic 023).

    Empty content OR a degenerate canned/wrong-language refusal both mean the
    forced-final strategy failed and the recovery ladder should continue. Every
    ladder step and its entry gate must use this — a step that checks only
    emptiness lets a canned refusal be finalized verbatim (live 2026-07-19).
    """
    text = (content or "").strip()
    if not text:
        return True
    return is_degenerate_refusal(text, input_text)


def is_degenerate_refusal(answer: str | None, input_text: str = "") -> bool:
    """A canned, wrong-language, or pathologically repetitive non-answer.

    Two shapes: (a) a known «I'm just an AI, I haven't learned to answer this» template, or (b) a short
    answer dominated by a script (CJK/Kana/Hangul) that mismatches a non-CJK input — e.g. a Chinese
    fallback to a Russian question. Both are degenerate outputs a bounded retry usually resolves, since
    the model can answer correctly from the same context (verified: deepseek-v4-flash returns the canned
    Chinese refusal ~60% and the correct answer ~40% for the same query). Empty answers are handled
    separately; this returns False for them.
    """
    stripped = (answer or "").strip()
    if not stripped:
        return False
    low = stripped.lower()
    if any(marker in low for marker in _CANNED_REFUSAL_MARKERS):
        return True
    tokens = re.findall(r"\S+", stripped)
    if len(tokens) >= _REPETITION_MIN_TOKENS:
        dominant_count = Counter(tokens).most_common(1)[0][1]
        if (
            dominant_count / len(tokens) >= _REPETITION_DOMINANT_RATIO
            and len(set(tokens)) / len(tokens) <= _REPETITION_MAX_UNIQUE_RATIO
        ):
            return True
    if (
        len(stripped) < 400
        and _cjk_ratio(stripped) >= 0.4
        and _cjk_ratio(input_text) < 0.2
    ):
        return True
    return False


_ASSISTANT_COMPLETED_EVENTS = {
    "assistant_message_completed",
    "assistant_message_replaced",
}


def _event_name(event: Any) -> str:
    # RuntimeEvent stores the name in ``.type`` (a RuntimeEventType enum); event-log dicts and adapter
    # projections use ``event``/``type`` string keys. Normalise all of them, unwrapping enum ``.value``.
    if isinstance(event, dict):
        name: Any = event.get("event") or event.get("type") or ""
    else:
        name = getattr(event, "type", None) or getattr(event, "event", "") or ""
    return str(getattr(name, "value", name) or "").strip()


def _event_payload(event: Any) -> dict:
    payload = (
        event.get("payload")
        if isinstance(event, dict)
        else getattr(event, "payload", None)
    )
    return payload if isinstance(payload, dict) else {}


def assistant_turn_contents(events: Any) -> list[str]:
    """Ordered assistant-turn contents from the run's event log (empty turns included)."""
    contents: list[str] = []
    for event in events or []:
        if _event_name(event) in _ASSISTANT_COMPLETED_EVENTS:
            contents.append(str(_event_payload(event).get("content") or ""))
    return contents


def recover_degenerate_terminal_answer(
    *,
    events: Any,
    terminal_answer: str | None,
    tool_call_count: int,
) -> tuple[str | None, str | None]:
    """Return ``(recovered_answer, reason)`` or ``(None, None)``.

    ``reason`` is ``empty_terminal_answer`` or ``degenerate_short_restatement``. A ``None`` recovery
    means the terminal answer stands (the common case — this is a no-op for well-behaved runs).
    """
    if tool_call_count and tool_call_count > 0:
        return None, None
    substantive = [
        c
        for c in assistant_turn_contents(events)
        if len(c.strip()) >= SUBSTANTIVE_ANSWER_CHARS
    ]
    if not substantive:
        return None, None
    best = max(substantive, key=lambda c: len(c.strip()))
    term = (terminal_answer or "").strip()
    if term == best.strip():
        return None, None
    if not term:
        return best, "empty_terminal_answer"
    # A pure-text run (0 tool calls) reaching a terminal answer that is dwarfed by an earlier
    # substantive turn re-answered without progress — recover the real answer. Ratio-based rather than
    # an absolute length so a borderline-short degenerate (e.g. ~200 chars vs a ~5k answer) is caught.
    if len(best.strip()) >= MIN_SUBSTANTIVE_RATIO * max(1, len(term)):
        return best, "degenerate_short_restatement"
    return None, None


__all__ = [
    "assistant_turn_contents",
    "final_content_unusable",
    "recover_degenerate_terminal_answer",
    "is_degenerate_refusal",
    "SUBSTANTIVE_ANSWER_CHARS",
    "MIN_SUBSTANTIVE_RATIO",
]
