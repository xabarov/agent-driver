"""Abstract reasoning-effort tiers + provider-neutral envelope mapping (R1).

A single ordinal effort ladder that callers set once (``AgentRunInput.reasoning_effort``)
and the runtime resolves at request-build time into the provider-neutral ``reasoning``
envelope carried on ``LlmRequest.reasoning`` (which mirrors OpenRouter's unified
``reasoning`` body param). Each provider then interprets that envelope:

  * the OpenAI-compatible provider forwards it verbatim (OpenRouter passthrough);
  * the Anthropic provider translates it into a native ``thinking`` block
    (see ``providers_impl/anthropic.py``).

Kept in the ``contracts`` layer so ``AgentRunInput`` (contracts) and the providers (llm)
can both depend on it without a layering inversion. Provider-specific wire formats
(Anthropic ``budget_tokens`` vs adaptive ``effort``, Gemini ``thinkingBudget``, …) live
in the providers, not here — this module owns only the neutral tier vocabulary and the
neutral envelope shape.
"""

from __future__ import annotations

from typing import Any

# Ordinal ladder, lowest → highest reasoning. ``"none"`` disables thinking entirely.
# This exact vocabulary is the convergent one across Claude Code, openhands-sdk,
# hermes-agent and openclaude.
REASONING_EFFORT_TIERS: tuple[str, ...] = (
    "none",
    "minimal",
    "low",
    "medium",
    "high",
    "xhigh",
    "max",
)


def normalize_reasoning_effort(value: str | None) -> str | None:
    """Lower/strip an effort tier; ``None``/``""`` → ``None``; raise on an unknown tier."""
    if value is None:
        return None
    tier = str(value).strip().lower()
    if not tier:
        return None
    if tier not in REASONING_EFFORT_TIERS:
        raise ValueError(
            "reasoning_effort must be one of "
            f"{REASONING_EFFORT_TIERS!r} or None, got {value!r}"
        )
    return tier


def effort_to_reasoning_envelope(value: str | None) -> dict[str, Any] | None:
    """Abstract effort tier → provider-neutral ``reasoning`` envelope.

    * ``None`` → ``None`` (omitted; the provider's own default applies).
    * ``"none"`` → ``{"enabled": False}`` (thinking explicitly disabled — a no-op on
      non-thinking backends, verified safe).
    * any graded tier → ``{"effort": tier}`` (OpenRouter's unified shape; the Anthropic
      provider maps this to a native thinking block).

    Note: ``low``/``medium``/``high`` are accepted by every reasoning backend; the finer
    tiers (``minimal``/``xhigh``/``max``) are honored natively by Anthropic and the newest
    OpenAI models and may be clamped or rejected by other OpenRouter routes — callers that
    target such routes should stick to the core three.
    """
    tier = normalize_reasoning_effort(value)
    if tier is None:
        return None
    if tier == "none":
        return {"enabled": False}
    return {"effort": tier}
