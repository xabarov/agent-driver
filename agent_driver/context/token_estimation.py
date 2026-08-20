"""Shared char↔token estimation with per-run calibration (BUG-6).

The runtime estimates token counts from character counts for the pre-send pressure
trigger and budget conversions. A fixed 4 chars/token is an English-prose average and
is wrong for CJK/RU (under-counts) or code (over-counts), so the compaction trigger
fires at the wrong time on such content.

We cannot run a real tokenizer in the runtime by default (domain-neutral, no default
heavy dependency, no runtime network). But after every provider response the runtime
already has the provider's ACTUAL input token count, and it knows the chars it sent —
so the true ratio for this model+content is observable at zero cost. We fold that
observation into a bounded EMA and use it for the next pre-send estimate: a
self-correcting, dependency-free calibration. A host wanting exact counts can inject a
``TokenCounter`` later (phase 2); the default stays estimation.
"""

from __future__ import annotations

DEFAULT_CHARS_PER_TOKEN = 4.0
# Pre-resolution default context window (tokens), single-sourced here (BUG-2) so the
# pressure / build / config planes never drift on the literal — it was previously a
# bare ``12000`` repeated in three files. This is ONLY the fail-safe used before
# per-model resolution: at run start the runtime resolves the REAL window from the
# model id (``agent_driver.llm.context_windows.resolve_context_window``) and, for an
# unresolved id, uses the modern 128k ``UNRESOLVED_MODEL_CONTEXT_WINDOW`` — so this
# legacy value only ever applies to inputs that never went through resolution.
DEFAULT_CONTEXT_WINDOW_ESTIMATE = 12_000
# Clamp range for a calibrated ratio: below ~2 is denser than any real tokenizer,
# above ~8 is sparser than plausible — either signals a bad datapoint, not content.
MIN_CHARS_PER_TOKEN = 2.0
MAX_CHARS_PER_TOKEN = 8.0
# Weight of a fresh observation in the EMA (resists per-turn noise while adapting).
_EMA_OBSERVATION_WEIGHT = 0.3


def estimate_tokens(chars: int, *, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN) -> int:
    """Estimate tokens from a character count using ``chars_per_token``."""
    if chars_per_token <= 0:
        return int(chars)
    return int(chars // chars_per_token)


def chars_for_tokens(
    tokens: int, *, chars_per_token: float = DEFAULT_CHARS_PER_TOKEN
) -> int:
    """The char budget for a token count using ``chars_per_token``."""
    return int(tokens * chars_per_token)


def clamp_chars_per_token(ratio: float) -> float:
    """Clamp a ratio into the sane [MIN, MAX] range."""
    return max(MIN_CHARS_PER_TOKEN, min(MAX_CHARS_PER_TOKEN, ratio))


def calibrate_chars_per_token(
    prior: float, *, chars_sent: int, actual_input_tokens: int | None
) -> float:
    """EMA-update ``prior`` toward the observed ``chars_sent / actual_input_tokens``.

    Returns ``prior`` unchanged on a degenerate observation (missing usage, zero
    tokens, or no chars) so a bad datapoint can never wreck the estimate. The result
    is clamped to the sane range.
    """
    if not actual_input_tokens or actual_input_tokens <= 0 or chars_sent <= 0:
        return clamp_chars_per_token(prior)
    observed = clamp_chars_per_token(chars_sent / actual_input_tokens)
    updated = (1.0 - _EMA_OBSERVATION_WEIGHT) * prior + _EMA_OBSERVATION_WEIGHT * observed
    return clamp_chars_per_token(updated)
