"""Cache-safe aux-call substrate (epic 034).

Side LLM work — compaction summaries, memory fact extraction, structured emits,
follow-up suggestions, session titles — used to be ad-hoc: each call resolved its
own backend, none merged usage into the run's cost ledger, and none could ride
the parent's prompt cache. This is the single substrate they share.

Reference-first (openclaude ``src/utils/forkedAgent.ts``) — four guarantees:

1. **Cache-safe.** A fork that passes a ``cache_prefix`` rides the parent's prompt
   cache by sending an IDENTICAL system+messages prefix with ``enable_prompt_cache``.
   The hard rule (openclaude, verbatim): *do NOT override model / tools / thinking
   on a cache-sharing fork* — those are part of the cache key, and changing them
   busts it (PR #18143 set ``effort:'low'`` and caused a 45× cache-write spike,
   92.7%→61% hit rate). Deny tools via policy, never by shrinking the tools array.
2. **Usage tracked.** The call's usage merges into ``cost_ledger`` tagged by task,
   so the receipt stays honest without threading a callback through every caller
   (today only compaction merges; memory extraction / structured emits are lost).
3. **Isolated.** A plain ``provider.complete`` — it never mutates the parent's
   message history or runtime state (mirrors the reference's no-op mutation
   callbacks); the caller owns the prompt it passes.
4. **Observable.** ``aux_fork_event_payload`` yields a raw-free marker
   (tokens / cache-hit / task) for an ``aux_fork_completed`` event (epic 037).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmRequest, LlmResponse

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class AuxCachePrefix:
    """The cache-critical prefix a fork shares with its parent to hit the cache.

    ``messages`` must be byte-identical to the parent's message prefix, and the
    fork must NOT alter ``model`` / ``tools`` / thinking vs the parent — only then
    is the provider cache key identical and the read a hit. Passing this is opt-in:
    a side-call with its own self-contained prompt (memory extraction) leaves it
    ``None`` and runs as a cheap independent request.
    """

    messages: tuple[ChatMessage, ...] = ()
    enable_prompt_cache: bool = True


def merge_aux_usage(
    cost_ledger: Any, response: LlmResponse | None, *, task: str
) -> UsageSummary | None:
    """Merge one aux call's usage into ``cost_ledger`` (tagged by task).

    ``cost_ledger`` is anything exposing ``accumulate(UsageSummary)`` — the run's
    :class:`~agent_driver.observability.cost_ledger.CostLedger` or the
    ``CostRuntimeState`` view. No-op (returns ``None``) when the ledger, response,
    or usage is missing, or the usage carries no model name (the ledger keys by
    model). The ``aux_task`` tag lands in ``usage.metadata`` so aux spend is
    attributable without a separate rail.
    """
    if cost_ledger is None or response is None:
        return None
    usage = getattr(response, "usage", None)
    if usage is None or not getattr(usage, "model_name", None):
        return None
    tagged = usage.model_copy(
        update={"metadata": {**(usage.metadata or {}), "aux_task": task}}
    )
    try:
        cost_ledger.accumulate(tagged)
    except Exception:  # noqa: BLE001 - accounting must never break the side-call
        logger.debug("aux usage merge skipped for task %s", task, exc_info=True)
        return None
    return tagged


def aux_fork_event_payload(
    response: LlmResponse | None, *, task: str
) -> dict[str, Any]:
    """Raw-free observability marker for a completed aux fork (epic 037).

    Counts only — never prompt/response text. Mirrors openclaude
    ``tengu_fork_agent_query`` (input/output/cache tokens + derived hit rate).
    """
    usage = getattr(response, "usage", None) if response else None
    inp = int(getattr(usage, "input_tokens", 0) or 0) if usage else 0
    out = int(getattr(usage, "output_tokens", 0) or 0) if usage else 0
    cache_read = int(getattr(usage, "cache_read_tokens", 0) or 0) if usage else 0
    denom = inp + cache_read
    return {
        "aux_task": task,
        "input_tokens": inp,
        "output_tokens": out,
        "cache_read_tokens": cache_read,
        "cache_hit_rate": round(cache_read / denom, 4) if denom else 0.0,
        "raw_free": True,
    }


async def aux_completion(
    *,
    provider: Any,
    messages: list[ChatMessage],
    model: str | None = None,
    task: str = "aux",
    cache_prefix: AuxCachePrefix | None = None,
    tools: list[dict[str, Any]] | None = None,
    tool_choice: str | dict[str, Any] | None = None,
    temperature: float | None = 0.0,
    reasoning: dict[str, Any] | None = None,
    max_tokens: int | None = None,
    metadata: dict[str, Any] | None = None,
    cost_ledger: Any = None,
) -> LlmResponse:
    """Run one cache-safe side-LLM call through the shared substrate.

    When ``cache_prefix`` is set, ``cache_prefix.messages`` are prepended and
    ``enable_prompt_cache`` is turned on so the call rides the parent's prompt
    cache. Usage is merged into ``cost_ledger`` (tagged ``task``). Returns the raw
    :class:`LlmResponse`; the caller shapes the result (structured parse, summary,
    etc.). Isolation is inherent — this only reads ``provider``; it mutates nothing.
    """
    if cache_prefix is not None and cache_prefix.messages:
        full_messages = [*cache_prefix.messages, *messages]
        enable_cache = cache_prefix.enable_prompt_cache
    else:
        full_messages = list(messages)
        enable_cache = False

    request = LlmRequest(
        messages=full_messages,
        model=model,
        tools=tools or [],
        tool_choice=tool_choice,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning=reasoning,
        enable_prompt_cache=enable_cache,
        metadata={"purpose": "aux_completion", "aux_task": task, **(metadata or {})},
    )
    response = await provider.complete(request)
    merge_aux_usage(cost_ledger, response, task=task)
    return response


__all__ = [
    "AuxCachePrefix",
    "aux_completion",
    "merge_aux_usage",
    "aux_fork_event_payload",
]
