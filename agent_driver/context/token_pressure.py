"""Token-pressure estimation for context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenPressureInput:  # pylint: disable=too-many-instance-attributes
    """Inputs for deterministic token pressure estimation."""

    prompt_messages: tuple[dict[str, Any], ...]
    observations: tuple[dict[str, Any], ...] = ()
    retained_digest_ids: tuple[str, ...] = ()
    retained_artifact_ids: tuple[str, ...] = ()
    context_window_estimate: int = 12000
    warning_threshold: int = 7500
    compact_threshold: int = 9000
    blocking_threshold: int = 10500
    output_token_reserve: int = 1500
    early_warning_ratio: float = 0.35
    delegate_or_summarize_ratio: float = 0.45
    blocking_ratio: float = 0.92


def estimate_token_pressure(inp: TokenPressureInput) -> dict[str, Any]:
    """Build deterministic pressure snapshot for runtime metadata/events."""
    prompt_chars = sum(
        len(str(item.get("content", ""))) for item in inp.prompt_messages
    )
    observation_chars = sum(
        len(str(item.get("text_preview", ""))) for item in inp.observations
    )
    used_tokens_estimate = (prompt_chars + observation_chars) // 4
    available_after_reserve = max(
        0,
        inp.context_window_estimate - inp.output_token_reserve,
    )
    remaining_tokens = max(0, available_after_reserve - used_tokens_estimate)
    context_usage_ratio = (
        round(used_tokens_estimate / inp.context_window_estimate, 4)
        if inp.context_window_estimate > 0
        else None
    )
    state = _pressure_state(inp, used_tokens_estimate, context_usage_ratio)
    return {
        "state": state,
        "used_tokens_estimate": used_tokens_estimate,
        "context_usage_ratio": context_usage_ratio,
        "remaining_tokens_estimate": remaining_tokens,
        "context_window_estimate": inp.context_window_estimate,
        "output_token_reserve": inp.output_token_reserve,
        "warning_threshold": inp.warning_threshold,
        "compact_threshold": inp.compact_threshold,
        "blocking_threshold": inp.blocking_threshold,
        "early_warning_ratio": inp.early_warning_ratio,
        "delegate_or_summarize_ratio": inp.delegate_or_summarize_ratio,
        "blocking_ratio": inp.blocking_ratio,
        "retained_digest_count": len(inp.retained_digest_ids),
        "retained_artifact_count": len(inp.retained_artifact_ids),
        "prompt_message_count": len(inp.prompt_messages),
        "observation_count": len(inp.observations),
    }


def _pressure_state(
    inp: TokenPressureInput,
    used_tokens_estimate: int,
    context_usage_ratio: float | None,
) -> str:
    """Return the phase-2 context pressure state."""
    if used_tokens_estimate >= inp.blocking_threshold or _ratio_at_least(
        context_usage_ratio, inp.blocking_ratio
    ):
        return "blocking"
    if used_tokens_estimate >= inp.compact_threshold:
        return "compact_recommended"
    if _ratio_at_least(context_usage_ratio, inp.delegate_or_summarize_ratio):
        return "delegate_or_summarize"
    if used_tokens_estimate >= inp.warning_threshold or _ratio_at_least(
        context_usage_ratio, inp.early_warning_ratio
    ):
        return "early_warning"
    return "ok"


def _ratio_at_least(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


__all__ = ["TokenPressureInput", "estimate_token_pressure"]
