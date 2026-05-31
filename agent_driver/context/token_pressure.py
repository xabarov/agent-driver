"""Token-pressure estimation for context assembly."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class TokenPressureInput:
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


def estimate_token_pressure(inp: TokenPressureInput) -> dict[str, Any]:
    """Build deterministic pressure snapshot for runtime metadata/events."""
    prompt_chars = sum(len(str(item.get("content", ""))) for item in inp.prompt_messages)
    observation_chars = sum(
        len(str(item.get("text_preview", ""))) for item in inp.observations
    )
    used_tokens_estimate = (prompt_chars + observation_chars) // 4
    available_after_reserve = max(0, inp.context_window_estimate - inp.output_token_reserve)
    remaining_tokens = max(0, available_after_reserve - used_tokens_estimate)
    context_usage_ratio = (
        round(used_tokens_estimate / inp.context_window_estimate, 4)
        if inp.context_window_estimate > 0
        else None
    )
    state = "ok"
    if used_tokens_estimate >= inp.blocking_threshold:
        state = "blocking"
    elif used_tokens_estimate >= inp.compact_threshold:
        state = "compact_recommended"
    elif used_tokens_estimate >= inp.warning_threshold:
        state = "warning"
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
        "retained_digest_count": len(inp.retained_digest_ids),
        "retained_artifact_count": len(inp.retained_artifact_ids),
        "prompt_message_count": len(inp.prompt_messages),
        "observation_count": len(inp.observations),
    }


__all__ = ["TokenPressureInput", "estimate_token_pressure"]
