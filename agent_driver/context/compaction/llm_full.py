"""No-tool full LLM compaction path."""

from __future__ import annotations

import json
from time import monotonic

from agent_driver.context.compaction.retry import ptl_retry_drop_oldest_groups
from agent_driver.context.compaction.sanitizers import sanitize_compaction_text
from agent_driver.context.compaction.prompts import (
    build_full_compaction_prompt,
    strip_private_draft,
)
from agent_driver.contracts import CompactionMode, CompactionResult
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
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
) -> tuple[CompactionResult, dict[str, object]]:
    """Run full no-tool compaction with structured validation."""
    sanitized_history = sanitize_compaction_text(history_excerpt)
    groups = [item for item in sanitized_history.splitlines() if item.strip()]
    kept_groups, dropped_groups = ptl_retry_drop_oldest_groups(
        groups=groups,
        max_chars=5000,
    )
    bounded_history = "\n".join(kept_groups)
    prompt = build_full_compaction_prompt(
        history_excerpt=bounded_history,
        user_request=user_request,
    )
    started = monotonic()
    response = await provider.complete(
        LlmRequest(
            model=model,
            messages=[ChatMessage(role="user", content=prompt)],
            metadata={"compaction_mode": "llm_full", "no_tools": True},
        )
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
                    "ptl_dropped_groups": len(dropped_groups),
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
                "ptl_dropped_groups": len(dropped_groups),
            },
        ),
        summary,
    )


__all__ = ["run_full_llm_compaction"]
