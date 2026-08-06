"""Deterministic resolution of typed and legacy run context budgets."""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from agent_driver.contracts.context.run_budget import (
    COMPACTION_WINDOW_CHAR_FRACTION,
    MAX_RUN_CONTEXT_ITEMS,
    MAX_RUN_PREVIEW_CHARS,
    ContextBudgetDefaults,
    ResolvedRunContextBudget,
    RunContextBudget,
)

_ESTIMATED_CHARS_PER_TOKEN = 4


def _scaled_cap(
    value: int | None,
    *,
    input_tokens: int,
    baseline_tokens: int,
    ceiling: int,
) -> int | None:
    if value is None or value < 0:
        return value
    scaled = (value * input_tokens + baseline_tokens - 1) // baseline_tokens
    return min(ceiling, max(value, scaled))


def _declared_budget(run_input: Any) -> tuple[RunContextBudget | None, str, bool]:
    typed = getattr(run_input, "context_budget", None)
    if isinstance(typed, RunContextBudget):
        return typed, "run_input.context_budget", False

    app_metadata = getattr(run_input, "app_metadata", None)
    raw = app_metadata.get("context_budget") if isinstance(app_metadata, dict) else None
    if not isinstance(raw, dict):
        return None, "", False
    try:
        return (
            RunContextBudget.model_validate(raw),
            "run_input.app_metadata.context_budget",
            True,
        )
    except ValidationError:
        return None, "", True


def resolve_run_context_budget(
    run_input: Any,
    defaults: ContextBudgetDefaults,
) -> ResolvedRunContextBudget:
    """Resolve typed caller policy, then legacy metadata, then runner defaults.

    ``AgentRunInput.context_budget`` is authoritative. For one deprecation
    window, ``app_metadata.context_budget`` with the same shape is accepted.
    Invalid legacy metadata is ignored and recorded without copying its values
    into the audit.
    """
    declared, source, legacy_seen = _declared_budget(run_input)
    if declared is None:
        return ResolvedRunContextBudget(
            source=defaults.source,
            input_tokens=max(1, defaults.max_chars // _ESTIMATED_CHARS_PER_TOKEN),
            output_tokens=defaults.output_token_reserve,
            max_chars=defaults.max_chars,
            max_messages=defaults.max_messages,
            max_observations=defaults.max_observations,
            protect_recent_messages=defaults.protect_recent_messages,
            preserve_recent_observations=defaults.preserve_recent_observations,
            max_observation_preview_chars=defaults.max_observation_preview_chars,
            context_window_estimate=defaults.context_window_estimate,
            warning_threshold=defaults.warning_threshold,
            compact_threshold=defaults.compact_threshold,
            blocking_threshold=defaults.blocking_threshold,
            output_token_reserve=defaults.output_token_reserve,
            max_compaction_chars=defaults.max_compaction_chars,
            audit={
                "strategy": "runner_defaults",
                "legacy_context_budget_rejected": legacy_seen,
            },
        )

    input_tokens = declared.input_tokens
    output_tokens = declared.output_tokens
    baseline_tokens = max(1, defaults.context_window_estimate)
    # BUG-1 (typed path): cap the summariser input at a FRACTION of the window char
    # budget (input_tokens * chars/token), not a fixed 262144 that bound below the
    # window on large-context models.
    window_char_cap = max(
        1,
        int(
            input_tokens
            * _ESTIMATED_CHARS_PER_TOKEN
            * COMPACTION_WINDOW_CHAR_FRACTION
        ),
    )
    max_compaction_chars = declared.max_compaction_chars
    if max_compaction_chars is None:
        max_compaction_chars = _scaled_cap(
            defaults.max_compaction_chars,
            input_tokens=input_tokens,
            baseline_tokens=baseline_tokens,
            ceiling=window_char_cap,
        )
    assert max_compaction_chars is not None
    max_compaction_chars = min(max_compaction_chars, window_char_cap)

    return ResolvedRunContextBudget(
        source=source,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        max_chars=input_tokens * _ESTIMATED_CHARS_PER_TOKEN,
        max_messages=(
            declared.max_messages
            if declared.max_messages is not None
            else _scaled_cap(
                defaults.max_messages,
                input_tokens=input_tokens,
                baseline_tokens=baseline_tokens,
                ceiling=MAX_RUN_CONTEXT_ITEMS,
            )
        ),
        max_observations=(
            declared.max_observations
            if declared.max_observations is not None
            else _scaled_cap(
                defaults.max_observations,
                input_tokens=input_tokens,
                baseline_tokens=baseline_tokens,
                ceiling=MAX_RUN_CONTEXT_ITEMS,
            )
        ),
        protect_recent_messages=(
            declared.protect_recent_messages
            if declared.protect_recent_messages is not None
            else _scaled_cap(
                defaults.protect_recent_messages,
                input_tokens=input_tokens,
                baseline_tokens=baseline_tokens,
                ceiling=MAX_RUN_CONTEXT_ITEMS,
            )
        ),
        preserve_recent_observations=(
            declared.preserve_recent_observations
            if declared.preserve_recent_observations is not None
            else _scaled_cap(
                defaults.preserve_recent_observations,
                input_tokens=input_tokens,
                baseline_tokens=baseline_tokens,
                ceiling=MAX_RUN_CONTEXT_ITEMS,
            )
        ),
        max_observation_preview_chars=(
            declared.max_observation_preview_chars
            if declared.max_observation_preview_chars is not None
            else _scaled_cap(
                defaults.max_observation_preview_chars,
                input_tokens=input_tokens,
                baseline_tokens=baseline_tokens,
                ceiling=MAX_RUN_PREVIEW_CHARS,
            )
        ),
        context_window_estimate=input_tokens + output_tokens,
        warning_threshold=max(1, int(input_tokens * 0.75)),
        compact_threshold=max(1, int(input_tokens * 0.90)),
        blocking_threshold=max(1, int(input_tokens * 0.98)),
        output_token_reserve=output_tokens,
        max_compaction_chars=max_compaction_chars,
        audit={
            "strategy": "typed_run_budget",
            "source": source,
            "legacy": source == "run_input.app_metadata.context_budget",
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "max_compaction_chars": max_compaction_chars,
        },
    )


__all__ = ["resolve_run_context_budget"]
