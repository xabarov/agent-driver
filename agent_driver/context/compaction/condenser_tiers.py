"""Concrete model-free ``Condenser`` tiers over the existing compaction primitives.

Option-B1b (compaction hardening C2): the cost-ordered :class:`CondenserPipeline`
(``condenser.py``) is wired as the live compaction seam behind an opt-in flag. These
adapters wrap the deterministic, LLM-free reduction primitives so the pipeline can
run them cheapest-first and make an expensive LLM summary a *no-op* whenever
clearing/truncating old tool bulk already brings the request under budget:

* :class:`ToolResultPruner` — idle-boundary wholesale clear of old tool-result
  content (`tool_clear.clear_old_tool_results`); the cheapest tier.
* :class:`ToolHistoryCondenser` — tiered size-truncation of old tool results
  (`tool_history.compress_tool_history`), structure-preserving for JSON.
* :class:`PartialCondenser` — deterministic prefix/suffix bullet summary
  (`partial.build_partial_compaction`); the last model-free resort.

The LLM tier (``llm_full``) is NOT a condenser here: when the model-free tiers do
not reach the target the stage delegates to the mature ``_apply_llm_full_compaction``
path rather than re-implementing excerpt building, provider resolution, and rolling
summary inside a condenser.
"""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.context.compaction.condenser import (
    CondenseContext,
    CondenseResult,
    message_chars,
)
from agent_driver.context.compaction.partial import build_partial_compaction
from agent_driver.context.compaction.tool_clear import clear_old_tool_results
from agent_driver.context.compaction.tool_history import compress_tool_history
from agent_driver.contracts.messages import ChatMessage

_DEFAULT_WINDOW_TOKENS = 128_000


def _window_tokens(ctx: CondenseContext) -> int:
    """Effective model window in tokens for tier sizing, from ctx.extras."""
    raw = ctx.extras.get("effective_window_tokens")
    if isinstance(raw, int) and raw > 0:
        return raw
    return _DEFAULT_WINDOW_TOKENS


@dataclass(frozen=True, slots=True)
class ToolResultPruner:
    """Cheapest tier: clear the content of old tool results, keeping the newest."""

    keep_recent: int = 3
    name: str = "tool_result_pruner"

    def applies(self, ctx: CondenseContext) -> bool:
        return True

    async def condense(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenseResult:
        out = clear_old_tool_results(messages, keep_recent=self.keep_recent)
        return CondenseResult(
            messages=out.messages,
            changed=out.cleared > 0,
            chars_freed=out.chars_saved,
            audit={"cleared": out.cleared, "chars_saved": out.chars_saved},
        )


@dataclass(frozen=True, slots=True)
class ToolHistoryCondenser:
    """Tiered size-truncation of old tool-result bulk (structure-preserving)."""

    name: str = "tool_history"

    def applies(self, ctx: CondenseContext) -> bool:
        return True

    async def condense(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenseResult:
        before = message_chars(messages)
        new_messages, audit = compress_tool_history(
            messages, effective_window=_window_tokens(ctx)
        )
        freed = before - message_chars(new_messages)
        return CondenseResult(
            messages=new_messages,
            changed=bool(audit.get("activated")) and freed > 0,
            chars_freed=max(0, freed),
            audit=audit,
        )


@dataclass(frozen=True, slots=True)
class PartialCondenser:
    """Deterministic prefix bullet summary — the last model-free resort."""

    retain_recent_messages: int = 6
    name: str = "partial"

    def applies(self, ctx: CondenseContext) -> bool:
        return True

    async def condense(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenseResult:
        before = message_chars(messages)
        out = build_partial_compaction(
            messages=[m.model_dump(mode="json") for m in messages],
            retain_recent_messages=self.retain_recent_messages,
            prefix_mode=True,
        )
        new_messages = [ChatMessage.model_validate(item) for item in out.prompt_messages]
        freed = before - message_chars(new_messages)
        strategy = str(out.metadata.get("strategy", ""))
        # Honest: a no-op or a rewrite that frees nothing leaves the list unchanged.
        made_progress = strategy != "no_op" and freed > 0
        return CondenseResult(
            messages=new_messages if made_progress else messages,
            changed=made_progress,
            chars_freed=freed if made_progress else 0,
            audit=dict(out.metadata),
        )


def default_condenser_tiers() -> list[object]:
    """The model-free tier order: cheapest (clear) → tiered → deterministic summary."""
    return [ToolResultPruner(), ToolHistoryCondenser(), PartialCondenser()]


__all__ = [
    "ToolResultPruner",
    "ToolHistoryCondenser",
    "PartialCondenser",
    "default_condenser_tiers",
]
