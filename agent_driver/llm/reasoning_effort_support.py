"""Per-model reasoning-effort capability table + reject-before-I/O validation.

opencode-adoption EPIC-07 (also the deepseek-survey candidate). Fixes the documented
``contracts/reasoning`` foot-gun: the finer effort tiers (``minimal``/``xhigh``/``max``)
are honored natively only by some backends and are **clamped or rejected mid-stream** by
other OpenRouter routes. Rather than discover that from a failed streaming response, the
provider validates the requested tier against a small curated capability table *before*
the network call and raises a clear :class:`UnsupportedReasoningEffortError`.

Design (matches ``context_windows.py``): family substrings matched against the lowercased
model id, first match wins; an **unknown** model is permissive (never rejected). To keep
false-rejects at zero we only ever validate the *fine* tiers — the universal
``none``/``low``/``medium``/``high`` are accepted (or harmlessly ignored) everywhere and
are never rejected here.
"""

from __future__ import annotations

from agent_driver.contracts.reasoning import (
    REASONING_EFFORT_TIERS,
    normalize_reasoning_effort,
)

# Tiers accepted (or harmlessly ignored) by every backend — never rejected pre-flight.
UNIVERSAL_EFFORT_TIERS: frozenset[str] = frozenset({"none", "low", "medium", "high"})

# The finer tiers whose support genuinely varies across routes.
FINE_EFFORT_TIERS: frozenset[str] = frozenset(REASONING_EFFORT_TIERS) - UNIVERSAL_EFFORT_TIERS

_ALL_TIERS: frozenset[str] = frozenset(REASONING_EFFORT_TIERS)
_OPENAI_REASONING_TIERS: frozenset[str] = frozenset(
    {"none", "minimal", "low", "medium", "high"}
)

# Family substring -> the FULL set of effort tiers that family accepts. Matched against
# the lowercased model id (vendor prefixes like "anthropic/claude-3.5" match too); the
# FIRST match wins, so more specific families precede generic ones. Only high-confidence
# capability facts belong here — a model not matched is UNKNOWN (permissive).
_FAMILY_SUPPORTED_EFFORTS: tuple[tuple[str, frozenset[str]], ...] = (
    # Anthropic native thinking maps effort -> budget_tokens and CLAMPS; it never
    # rejects a tier, so every tier is "supported".
    ("claude", _ALL_TIERS),
    ("anthropic", _ALL_TIERS),
    # OpenAI reasoning models: the reasoning_effort enum is minimal/low/medium/high;
    # xhigh/max are not OpenAI values and are rejected upstream.
    ("gpt-5", _OPENAI_REASONING_TIERS),
    ("o1", _OPENAI_REASONING_TIERS),
    ("o3", _OPENAI_REASONING_TIERS),
    ("o4", _OPENAI_REASONING_TIERS),
    # Non-reasoning OpenAI chat models accept no graded reasoning effort — only the
    # disable/omit path. Placed AFTER the reasoning families so gpt-5/o-series win.
    ("gpt-4", UNIVERSAL_EFFORT_TIERS),
    ("gpt-3.5", UNIVERSAL_EFFORT_TIERS),
)


class UnsupportedReasoningEffortError(ValueError):
    """A reasoning-effort tier the target model is known not to support."""

    def __init__(self, model: str, tier: str, supported: list[str]) -> None:
        self.model = model
        self.tier = tier
        self.supported = supported
        super().__init__(
            f"model {model!r} does not support reasoning_effort {tier!r}; "
            f"supported: {supported}. Use one of low/medium/high for portable routes."
        )


def supported_efforts_for_model(model: str | None) -> frozenset[str] | None:
    """Return the effort tiers a known model accepts, or ``None`` when unknown."""
    lowered = str(model or "").strip().lower()
    if not lowered:
        return None
    for family, tiers in _FAMILY_SUPPORTED_EFFORTS:
        if family in lowered:
            return tiers
    return None


def validate_effort_for_model(effort: str | None, model: str | None) -> None:
    """Raise :class:`UnsupportedReasoningEffortError` for a known-unsupported fine tier.

    Universal tiers (``none``/``low``/``medium``/``high``) and unknown models pass through
    silently — we reject only when the curated table is *confident* the call would fail.
    """
    tier = normalize_reasoning_effort(effort)
    if tier is None or tier in UNIVERSAL_EFFORT_TIERS:
        return
    supported = supported_efforts_for_model(model)
    if supported is None:
        return  # unknown model -> permissive (cannot be confident it will reject)
    if tier not in supported:
        raise UnsupportedReasoningEffortError(
            str(model), tier, sorted(supported)
        )


def effort_from_reasoning_envelope(envelope: object) -> str | None:
    """Recover the effort tier from a provider-neutral reasoning envelope.

    ``{"effort": tier}`` -> ``tier``; ``{"enabled": False}`` -> ``"none"``; anything else
    -> ``None`` (nothing to validate).
    """
    if not isinstance(envelope, dict):
        return None
    effort = envelope.get("effort")
    if isinstance(effort, str) and effort:
        return effort
    if envelope.get("enabled") is False:
        return "none"
    return None


__all__ = [
    "FINE_EFFORT_TIERS",
    "UNIVERSAL_EFFORT_TIERS",
    "UnsupportedReasoningEffortError",
    "effort_from_reasoning_envelope",
    "supported_efforts_for_model",
    "validate_effort_for_model",
]
