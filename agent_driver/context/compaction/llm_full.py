"""No-tool full LLM compaction path."""

from __future__ import annotations

import hashlib
import json
from time import monotonic

from agent_driver.context.compaction.prompts import (
    build_full_compaction_prompt,
    strip_private_draft,
)
from agent_driver.context.compaction.retry import ptl_retry_drop_oldest_groups
from agent_driver.context.compaction.sanitizers import sanitize_compaction_text
from agent_driver.contracts import CompactionMode, CompactionResult
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.liveness import AuxIdleTimeout, bounded_side_completion
from agent_driver.llm.providers import LlmProvider

REQUIRED_SUMMARY_KEYS = {
    "request_intent",
    "key_concepts",
    "files_code",
    "errors_fixes",
    "problems",
    "user_messages",
    "pending_tasks",
    "current_work",
    "next_step",
}


def _extract_persisted_summary_json(text: str) -> dict[str, object]:
    """Extract persisted summary JSON from model output."""
    start_tag = "<persisted_summary>"
    end_tag = "</persisted_summary>"
    start = text.find(start_tag)
    end = text.find(end_tag)
    if start == -1 or end == -1 or end < start:
        raise ValueError("missing persisted summary block")
    payload = text[start + len(start_tag) : end].strip()
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("persisted summary must be object")
    missing = REQUIRED_SUMMARY_KEYS - set(data)
    if missing:
        raise ValueError(f"missing summary keys: {sorted(missing)}")
    return data


async def run_full_llm_compaction(
    *,
    provider: LlmProvider,
    model: str,
    history_excerpt: str,
    user_request: str,
    idle_timeout_seconds: float | None = None,
    max_history_chars: int = 5000,
    history_is_bounded: bool = False,
) -> tuple[CompactionResult, dict[str, object]]:
    """Run full no-tool compaction with structured validation.

    Epic 041 C: with ``idle_timeout_seconds`` set the compaction call runs under a
    liveness (idle) timeout — a wedged summary provider fails as a bounded
    ``success=False`` result (the circuit breaker bounds retries) instead of hanging
    the whole run. A slow-but-healthy summary model is never killed (idle resets per
    streamed chunk).
    """
    sanitized_history = sanitize_compaction_text(history_excerpt)
    groups = [item for item in sanitized_history.splitlines() if item.strip()]
    if history_is_bounded:
        kept_groups, dropped_groups = groups, []
    else:
        protected_indexes = {0, len(groups) - 1} if groups else set()
        kept_groups, dropped_groups = ptl_retry_drop_oldest_groups(
            groups=groups,
            max_chars=max_history_chars,
            protected_indexes=protected_indexes,
        )
    bounded_history = "\n".join(kept_groups)
    reduction_metadata = {
        "ptl_history_max_chars": max_history_chars,
        "ptl_input_groups": len(groups),
        "ptl_retained_groups": len(kept_groups),
        "ptl_dropped_groups": len(dropped_groups),
        "ptl_dropped_group_sha256": [
            hashlib.sha256(item.encode("utf-8")).hexdigest()
            for item in dropped_groups
        ],
        "ptl_budget_overrun_chars": max(
            0,
            sum(len(item) for item in kept_groups) - max_history_chars,
        ),
        "ptl_history_prebounded": history_is_bounded,
    }
    prompt = build_full_compaction_prompt(
        history_excerpt=bounded_history,
        user_request=user_request,
    )
    started = monotonic()
    try:
        response = await bounded_side_completion(
            provider,
            LlmRequest(
                model=model,
                messages=[ChatMessage(role="user", content=prompt)],
                metadata={"compaction_mode": "llm_full", "no_tools": True},
            ),
            idle_timeout_seconds=idle_timeout_seconds,
        )
    except AuxIdleTimeout as exc:
        latency_ms = int((monotonic() - started) * 1000)
        return (
            CompactionResult(
                compaction_id="cmp_llm_full_idle_timeout",
                mode=CompactionMode.LLM_FULL,
                success=False,
                model=model,
                latency_ms=latency_ms,
                metadata={
                    "failure": str(exc),
                    "failure_kind": "aux_idle_timeout",
                    **reduction_metadata,
                },
            ),
            {},
        )
    cleaned, draft = strip_private_draft(response.message.content)
    try:
        summary = _extract_persisted_summary_json(cleaned)
    except (json.JSONDecodeError, ValueError) as exc:
        latency_ms = int((monotonic() - started) * 1000)
        return (
            CompactionResult(
                compaction_id="cmp_llm_full_failed",
                mode=CompactionMode.LLM_FULL,
                success=False,
                model=response.model,
                latency_ms=latency_ms,
                input_tokens_estimate=response.usage.input_tokens,
                output_tokens_estimate=response.usage.output_tokens,
                metadata={
                    "failure": str(exc),
                    **reduction_metadata,
                },
            ),
            {},
        )
    latency_ms = int((monotonic() - started) * 1000)
    return (
        CompactionResult(
            compaction_id="cmp_llm_full_ok",
            mode=CompactionMode.LLM_FULL,
            success=True,
            model=response.model,
            latency_ms=latency_ms,
            input_tokens_estimate=response.usage.input_tokens,
            output_tokens_estimate=response.usage.output_tokens,
            metadata={
                "draft_removed": draft is not None,
                **reduction_metadata,
            },
        ),
        summary,
    )


__all__ = ["run_full_llm_compaction"]
