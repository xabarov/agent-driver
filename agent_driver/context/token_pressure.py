"""Token-pressure estimation for context assembly."""

from __future__ import annotations

from typing import Any


def estimate_token_pressure(  # pylint: disable=too-many-arguments
    *,
    prompt_messages: list[dict[str, Any]],
    observations: list[dict[str, Any]],
    retained_digest_ids: list[str],
    retained_artifact_ids: list[str],
    context_window_estimate: int,
    warning_threshold: int,
    compact_threshold: int,
    blocking_threshold: int,
    output_token_reserve: int,
) -> dict[str, Any]:
    """Build deterministic pressure snapshot for runtime metadata/events."""
    prompt_chars = sum(len(str(item.get("content", ""))) for item in prompt_messages)
    observation_chars = sum(
        len(str(item.get("text_preview", ""))) for item in observations
    )
    # Simple heuristic: ~4 chars/token.
    used_tokens_estimate = (prompt_chars + observation_chars) // 4
    available_after_reserve = max(0, context_window_estimate - output_token_reserve)
    remaining_tokens = max(0, available_after_reserve - used_tokens_estimate)
    state = "ok"
    if used_tokens_estimate >= blocking_threshold:
        state = "blocking"
    elif used_tokens_estimate >= compact_threshold:
        state = "compact_recommended"
    elif used_tokens_estimate >= warning_threshold:
        state = "warning"
    return {
        "state": state,
        "used_tokens_estimate": used_tokens_estimate,
        "remaining_tokens_estimate": remaining_tokens,
        "context_window_estimate": context_window_estimate,
        "output_token_reserve": output_token_reserve,
        "warning_threshold": warning_threshold,
        "compact_threshold": compact_threshold,
        "blocking_threshold": blocking_threshold,
        "retained_digest_count": len(retained_digest_ids),
        "retained_artifact_count": len(retained_artifact_ids),
        "prompt_message_count": len(prompt_messages),
        "observation_count": len(observations),
    }


__all__ = ["estimate_token_pressure"]
