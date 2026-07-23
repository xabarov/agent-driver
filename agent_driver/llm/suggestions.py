"""Suggested next questions — reusable generation + reject filter (epic 038).

Reference-first port of openclaude ``src/services/PromptSuggestion``. That feature
predicts a SINGLE line the user would autocomplete into a CLI; this port adapts it
to **1-3 clickable follow-up questions** under a corpus assistant's answer. The
reusable, model-agnostic core — the reject filter (openclaude's hard-won
``shouldFilterSuggestion``), the cost-gate (``MAX_PARENT_UNCACHED_TOKENS``), and
the generation contract — lives here so any headless host can reuse it. The
invocation site and delivery are the host's thin integration: MeetScript attaches
the result to its terminal event because its SSE stream closes on run-completion,
so a post-completion background emit would never reach the client.

Design notes vs the reference:

- **Questions are wanted, not rejected.** openclaude rejects question-shaped
  suggestions (autocomplete wants imperatives); chips ARE questions, so that
  reject is dropped and the single-word affirmative allowlist is irrelevant.
- **Filter stays.** Assistant-voice, evaluative, meta, error-echo, prefixed-label,
  formatting, multi-sentence, and length bounds are all ported (bilingual RU/EN,
  because the product answers in Russian and a weak model occasionally slips into
  English). Without the filter the feature "generates shame" (ref comment).
- **Best-effort.** Generation failure returns ``[]`` (no chips) and never raises —
  a decorative affordance must not break the answer. This is the deliberate
  inverse of :func:`structured_completion`'s "never salvage" stance, which governs
  the PRIMARY result; here empty-is-fine is the correct terminal state.
- **Coalescing (openclaude superseded-abort) is structural here:** each turn
  generates its own chips synchronously within the turn, so there is no cross-turn
  in-flight generation to cancel — a new turn simply attaches fresh chips to its
  own answer. The substantive phase-B lever is the cost-gate, which is honored.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest

logger = logging.getLogger(__name__)

# openclaude MAX_PARENT_UNCACHED_TOKENS: a follow-up generation re-processes the
# answering turn's uncached tokens; past this it costs as much as the answer and
# is suppressed. Kept as the reference default; hosts may override.
MAX_PARENT_UNCACHED_TOKENS = 10_000

# Length bounds for a single chip (a question). Wider than the ref's 2-12 words /
# 100 chars because a grounded question ("Кто владелец решения по деплою офиса?")
# runs longer than a CLI autocomplete.
_MIN_WORDS = 2
_MAX_WORDS = 16
_MAX_CHARS = 120

# Adapted from openclaude SUGGESTION_PROMPT: predict what the USER would type next,
# not what they "should" ask. The test — "would they think 'I was just about to
# ask that'?" — is what keeps chips clickable instead of preachy. Output is plain
# text (one question per line) — the reference itself generates a plain-text
# suggestion and filters it, NOT a structured/tool channel; a forced tool call is
# both unfaithful and unreliable for the small aux models this feature runs on
# (live: gemini-flash-lite via OpenRouter returns no tool call at all).
SUGGESTION_SYSTEM_PROMPT = (
    "Ты предлагаешь 1-3 коротких follow-up вопроса, которые пользователь "
    "СЛЕДУЮЩИМ задал бы ассистенту по встречам — не то, что ему «стоило бы» "
    "спросить.\n\n"
    "ТЕСТ: подумает ли пользователь «я как раз собирался это спросить»?\n\n"
    "Правила:\n"
    "- Вопросы естественно продолжают разговор: уточнить деталь из ответа, "
    "раскрыть смежный факт по тем же встречам, попросить срок/владельца/сумму.\n"
    "- Пиши по-русски, как продолжил бы сам пользователь. 3-14 слов на вопрос.\n"
    "- Только вопросы, которые реально можно ответить по материалам встреч.\n"
    "- НИКОГДА: оценки («спасибо», «выглядит хорошо»), голос ассистента "
    "(«Давайте я…», «Вот…», «Могу…»), мета («нет вопросов», «не могу"
    " предложить»), выдуманные темы, форматирование, несколько предложений.\n\n"
    "ФОРМАТ: только сами вопросы, ПО ОДНОМУ НА СТРОКУ, без нумерации и маркеров. "
    "Если очевидного следующего вопроса нет — верни пустую строку."
)

# --- reject filter (openclaude shouldFilterSuggestion, adapted) ----------------

_META_RE = re.compile(
    r"^(нет\s+вопрос|не\s+могу\s+предлож|ничего\s+не|нет\s+предлож|"
    r"nothing\s+to\s+suggest|no\s+suggestion|nothing\s+found)",
    re.IGNORECASE,
)
_META_WRAPPED_RE = re.compile(r"^\(.*\)$|^\[.*\]$")
_ERROR_RE = re.compile(
    r"^(api error:|prompt is too long|request timed out|invalid api key|"
    r"image was too large|ошибка)",
    re.IGNORECASE,
)
_PREFIXED_LABEL_RE = re.compile(r"^\s*[\wА-Яа-яЁё]+:\s")
_MULTI_SENTENCE_RE = re.compile(r"[.!?]\s+[A-ZА-ЯЁ]")
_FORMATTING_RE = re.compile(r"[\n*]|\*\*")
_EVALUATIVE_RE = re.compile(
    r"спасибо|благодар|отлично|прекрасно|хорошо выглядит|звучит хорошо|"
    r"это всё|понятно|супер|thanks|thank you|looks good|sounds good|"
    r"that works|makes sense|awesome|excellent",
    re.IGNORECASE,
)
_ASSISTANT_VOICE_RE = re.compile(
    r"^(давайте|давай|вот |сейчас я|я могу|я мог|я сделаю|я предлагаю|я думаю|"
    r"позвольте|let me|i'll|i've|i'm|i can|i would|i think|here's|here is|"
    r"here are)",
    re.IGNORECASE,
)


def filter_suggestion(suggestion: str | None) -> str | None:
    """Return a cleaned suggestion if it passes every reject check, else ``None``.

    Mirrors openclaude ``shouldFilterSuggestion`` (inverted: this returns the kept
    text). ``None``/rejects are logged at debug with the reason so a poor prompt is
    diagnosable from telemetry, per the reference's suppression logging.
    """
    if not suggestion:
        return _reject("empty", suggestion)
    text = suggestion.strip()
    if not text:
        return _reject("empty", suggestion)
    lower = text.lower()
    words = text.split()
    wc = len(words)

    if _META_RE.match(lower):
        return _reject("meta_text", text)
    if _META_WRAPPED_RE.match(text):
        return _reject("meta_wrapped", text)
    if _ERROR_RE.match(lower):
        return _reject("error_message", text)
    if _PREFIXED_LABEL_RE.match(text):
        return _reject("prefixed_label", text)
    if _FORMATTING_RE.search(text):
        return _reject("has_formatting", text)
    if _MULTI_SENTENCE_RE.search(text):
        return _reject("multiple_sentences", text)
    if _EVALUATIVE_RE.search(lower):
        return _reject("evaluative", text)
    if _ASSISTANT_VOICE_RE.match(text):
        return _reject("claude_voice", text)
    if wc < _MIN_WORDS:
        return _reject("too_few_words", text)
    if wc > _MAX_WORDS:
        return _reject("too_many_words", text)
    if len(text) > _MAX_CHARS:
        return _reject("too_long", text)
    return text


def _reject(reason: str, suggestion: str | None) -> None:
    logger.debug("suggested question suppressed (%s): %r", reason, suggestion)
    return None


def suppress_reason_for_usage(
    usage: Mapping[str, Any] | None,
    *,
    max_uncached_tokens: int = MAX_PARENT_UNCACHED_TOKENS,
) -> str | None:
    """Cost-gate: don't generate follow-ups after an expensive/uncached turn.

    openclaude ``getParentCacheSuppressReason`` — the follow-up re-processes the
    answering turn's input + cache-write + output tokens (the fork never reads the
    parent's cache for its own prompt); if that uncached portion exceeds the
    budget the suggestion isn't worth the cost. Accepts a usage mapping with any
    of ``input_tokens`` / ``prompt_tokens``, ``cache_creation_input_tokens`` /
    ``cache_write_tokens``, ``output_tokens`` / ``completion_tokens``. Returns
    ``"cache_cold"`` to suppress, else ``None``.
    """
    if not usage:
        return None

    def _pick(*keys: str) -> int:
        for k in keys:
            v = usage.get(k)
            if isinstance(v, (int, float)):
                return int(v)
        return 0

    uncached = (
        _pick("input_tokens", "prompt_tokens", "input")
        + _pick("cache_creation_input_tokens", "cache_write_tokens")
        + _pick("output_tokens", "completion_tokens", "output")
    )
    return "cache_cold" if uncached > max_uncached_tokens else None


def _dedup(items: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        key = item.lower().rstrip("?!. ")
        if key and key not in seen:
            seen.add(key)
            out.append(item)
    return out


async def generate_suggestions(
    *,
    provider: Any,
    question: str,
    answer: str,
    corpus_overview: str | None = None,
    model: str | None = None,
    max_suggestions: int = 3,
    usage: Mapping[str, Any] | None = None,
    max_uncached_tokens: int = MAX_PARENT_UNCACHED_TOKENS,
    answer_char_budget: int = 2000,
    metadata: Mapping[str, Any] | None = None,
) -> list[str]:
    """Generate up to ``max_suggestions`` follow-up questions for the last answer.

    Best-effort: returns ``[]`` on cost-gate suppression, empty question/answer, an
    all-filtered batch, or any generation error — never raises. The list is deduped
    (case-insensitive, punctuation-insensitive) and capped. ``provider`` should be
    the host's PII-boundary provider when the question/answer may carry names — this
    call sees both. ``model`` selects a per-task aux model (epic 032 aux seam); pass
    the host's fast grader model to keep the added latency small.
    """
    if not (question or "").strip() or not (answer or "").strip():
        return []
    reason = suppress_reason_for_usage(usage, max_uncached_tokens=max_uncached_tokens)
    if reason:
        logger.debug("suggested questions suppressed (%s)", reason)
        return []

    trimmed_answer = answer.strip()
    if len(trimmed_answer) > answer_char_budget:
        trimmed_answer = trimmed_answer[:answer_char_budget] + "…"
    parts = [f"Вопрос пользователя:\n{question.strip()}", f"Ответ:\n{trimmed_answer}"]
    if (corpus_overview or "").strip():
        parts.append(f"Доступные встречи (обзор):\n{corpus_overview.strip()}")
    parts.append(
        f"Предложи 1-{max_suggestions} следующих вопроса пользователя "
        "по правилам системного промпта — по одному на строку."
    )
    request = LlmRequest(
        model=model or None,
        messages=[
            ChatMessage(role=ChatRole.SYSTEM, content=SUGGESTION_SYSTEM_PROMPT),
            ChatMessage(role=ChatRole.USER, content="\n\n".join(parts)),
        ],
        temperature=0.3,
        max_tokens=200,
        metadata=dict(metadata) if metadata else {"purpose": "suggested_questions"},
    )

    try:
        response = await provider.complete(request)
        content = response.message.content or ""
    except Exception:  # noqa: BLE001 — decorative feature must never break the answer
        logger.warning("suggested questions generation errored", exc_info=True)
        return []

    kept = [
        cleaned
        for line in _split_lines(content)
        if (cleaned := filter_suggestion(line)) is not None
    ]
    return _dedup(kept)[:max_suggestions]


# Strip common list decorations a model adds despite «по одному на строку»:
# leading bullets/numbering/quotes, one question per line.
_LINE_DECORATION_RE = re.compile(r"^\s*(?:[-*•–—]\s*|\d+[.)]\s*|[\"'«»]\s*)+")


def _split_lines(content: str) -> list[str]:
    lines: list[str] = []
    for raw_line in (content or "").splitlines():
        cleaned = _LINE_DECORATION_RE.sub("", raw_line).strip().strip("\"'«»").strip()
        if cleaned:
            lines.append(cleaned)
    return lines


__all__ = [
    "MAX_PARENT_UNCACHED_TOKENS",
    "SUGGESTION_SYSTEM_PROMPT",
    "filter_suggestion",
    "generate_suggestions",
    "suppress_reason_for_usage",
]
