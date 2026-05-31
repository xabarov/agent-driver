"""Phase 11 H14 — reactive compaction on context-window provider errors.

Today: Phase 8 compaction triggers on proactive token-pressure thresholds
crossed by ``apply_compaction_if_eligible``. Edge case: a single oversized
tool output can push the next LLM call past the provider's context
window between two threshold checks, returning a ``context_length_exceeded``
/ ``max_tokens`` style error.

This module exposes:

* :func:`is_context_window_error` — provider-agnostic detector that
  looks at ``httpx.HTTPStatusError`` body for known patterns
  (OpenAI ``context_length_exceeded``, Anthropic
  ``input_too_long``, Gemini ``invalid_argument`` with relevant
  message, generic ``maximum context length`` / ``token limit``
  text).
* :func:`record_reactive_compaction` — appends an attempt to
  ``context.metadata['reactive_compactions']`` with optional
  outcome label. Used by the LLM-step retry path to track attempts
  and enforce a circuit breaker.
* :func:`reactive_compaction_count` — read-only count from metadata.
* :data:`REACTIVE_COMPACTION_MAX_ATTEMPTS` — circuit-breaker cap (2 by
  default; after that the runtime escalates to RUN_FAILED).

The actual retry loop in ``llm_step`` wires through these helpers
in the H14 follow-up commit; this module provides the stable
detector + accounting contract so tests and integration can land
incrementally without re-touching the LLM-step trace.
"""

from __future__ import annotations

import json
import logging
from typing import Any

try:
    import httpx  # type: ignore[import]
except ImportError:  # pragma: no cover - httpx is a hard dep
    httpx = None  # type: ignore[assignment]

logger = logging.getLogger(__name__)


# Circuit breaker — at most this many reactive compactions per run.
# After the cap, ``llm_step`` escalates to ``RUN_FAILED`` with a clear
# reason so the operator can investigate (often a runaway tool output).
REACTIVE_COMPACTION_MAX_ATTEMPTS = 2


# Known per-provider error tokens. Lowercased substring match against
# the JSON-decoded error body OR raw text body.
_CONTEXT_WINDOW_TOKENS: tuple[str, ...] = (
    # OpenAI: ``{"error":{"code":"context_length_exceeded", ...}}``
    "context_length_exceeded",
    # Anthropic: ``{"error":{"type":"invalid_request_error", "message":"input is too long ..."}}``
    "input is too long",
    "input_too_long",
    # Gemini / Vertex: ``"... exceeds maximum context length ..."``
    "exceeds maximum context length",
    "maximum context length",
    # Generic / vendor-agnostic phrasing.
    "token limit exceeded",
    "context window",
    "prompt is too long",
    "request payload size exceeds",
)


def is_context_window_error(exc: BaseException) -> bool:
    """Return True when ``exc`` is a context-window-exceeded provider error.

    Args:
        exc: any caught exception. Typically an ``httpx.HTTPStatusError``
            from a 400-class response, but plain strings inside
            ``RuntimeError`` / ``ValueError`` are handled too (some
            providers raise non-HTTP exceptions for stream-side errors).

    Returns:
        True only when at least one known token from
        :data:`_CONTEXT_WINDOW_TOKENS` appears (case-insensitive) in the
        error body / message. False for transport errors, auth, rate
        limit, and similar non-window 4xx/5xx codes.
    """
    body_text = _extract_error_text(exc)
    if not body_text:
        return False
    lowered = body_text.lower()
    return any(token in lowered for token in _CONTEXT_WINDOW_TOKENS)


def _extract_error_text(exc: BaseException) -> str:
    """Best-effort: pull useful text from a provider error.

    Handles ``httpx.HTTPStatusError.response.text``, JSON bodies via
    ``response.json()``, and falls back to ``str(exc)`` for non-HTTP
    paths.
    """
    parts: list[str] = []
    if httpx is not None and isinstance(exc, httpx.HTTPStatusError):
        try:
            text = exc.response.text or ""
            parts.append(text)
        except Exception:  # pragma: no cover - response detachment
            pass
        try:
            data = exc.response.json()
            parts.append(json.dumps(data, ensure_ascii=False))
        except Exception:
            pass
    parts.append(str(exc))
    return " ".join(p for p in parts if p)


def reactive_compaction_count(metadata: dict[str, Any]) -> int:
    """Return the number of reactive-compaction attempts recorded so far."""
    raw = metadata.get("reactive_compactions")
    if not isinstance(raw, list):
        return 0
    return sum(1 for entry in raw if isinstance(entry, dict))


def record_reactive_compaction(
    metadata: dict[str, Any],
    *,
    outcome: str,
    reason: str | None = None,
) -> int:
    """Record one reactive-compaction attempt in ``context.metadata``.

    Args:
        metadata: run-context metadata dict (mutated in place).
        outcome: short label, e.g. ``"attempted"``, ``"succeeded"``,
            ``"failed"``, ``"escalated"``. Free-form for the host but
            recommend the above small set for observability.
        reason: optional detail (e.g. specific provider error token
            that triggered the attempt).

    Returns:
        Total attempt count AFTER recording (1, 2, ...).
    """
    entry: dict[str, Any] = {"outcome": outcome}
    if reason:
        entry["reason"] = reason
    existing = metadata.get("reactive_compactions")
    if isinstance(existing, list):
        existing.append(entry)
    else:
        metadata["reactive_compactions"] = [entry]
    count = reactive_compaction_count(metadata)
    logger.info(
        "reactive_compaction attempt #%d (outcome=%s reason=%s)",
        count,
        outcome,
        reason,
    )
    return count


def should_escalate(metadata: dict[str, Any]) -> bool:
    """Return True when the circuit-breaker cap has been reached."""
    return reactive_compaction_count(metadata) >= REACTIVE_COMPACTION_MAX_ATTEMPTS


__all__ = [
    "REACTIVE_COMPACTION_MAX_ATTEMPTS",
    "is_context_window_error",
    "reactive_compaction_count",
    "record_reactive_compaction",
    "should_escalate",
]
