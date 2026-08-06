"""Condenser protocol + cost-ordered pipeline (Option B1 foundation).

A ``Condenser`` reduces a message list toward a char target; a ``CondenserPipeline``
runs a sequence of them cheapest-first and stops as soon as the request fits, so a
cheap deterministic tier (tool-result clearing) can make an expensive LLM summary a
no-op. Two guarantees ported from OpenHands / hermes:

* ``minimum_progress`` — a condenser whose freed chars fall below a floor of the
  input size is rejected (anti-thrash), instead of looping on ineffective passes.
* honest ``exhausted`` — the pipeline reports whether the result actually fits; a
  lossy tier that could not free meaningful space never masquerades as success
  (fixes BUG-7 at the pipeline level).

This module is the behaviour-neutral foundation: the live compaction dispatch is NOT
yet wired onto it (that cutover is B1b, a flagged behaviour change — the current
mode-decision tree is not a clean pipeline). See
``docs/epics/compaction-improvement/DESIGN-optionB1-condenser-protocol.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from agent_driver.contracts.messages import ChatMessage


def message_chars(messages: list[ChatMessage]) -> int:
    """Total content characters across ``messages`` (the pipeline's fit metric)."""
    return sum(len(str(getattr(m, "content", "") or "")) for m in messages)


@dataclass(frozen=True, slots=True)
class CondenseContext:
    """What a condenser needs to decide and reduce. Extended as condensers are
    ported; the pipeline itself only reads ``target_chars``."""

    target_chars: int
    max_compaction_chars: int | None = None
    chars_per_token: float = 4.0
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class CondenseResult:
    """The outcome of one condenser pass — honest about what it freed."""

    messages: list[ChatMessage]
    changed: bool
    chars_freed: int
    audit: dict[str, Any] = field(default_factory=dict)
    exhausted: bool = False


@runtime_checkable
class Condenser(Protocol):
    """A composable context-reduction strategy."""

    name: str

    def applies(self, ctx: CondenseContext) -> bool:
        """Cheap gate — skip ``condense`` entirely when this returns False."""
        ...

    async def condense(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenseResult:
        """Return a (possibly) reduced message list plus an honest receipt."""
        ...


@dataclass(frozen=True, slots=True)
class CondenserPipelineResult:
    """Aggregate outcome of a pipeline run."""

    messages: list[ChatMessage]
    applied: list[dict[str, Any]]
    fit: bool
    exhausted: bool


class CondenserPipeline:
    """Run condensers cheapest-first, stopping as soon as the target is met."""

    def __init__(
        self, condensers: list[Condenser], *, minimum_progress: float = 0.0
    ) -> None:
        self._condensers = list(condensers)
        # Fraction of the input size a pass must free to be accepted (anti-thrash).
        self._minimum_progress = max(0.0, min(1.0, minimum_progress))

    async def run(
        self, messages: list[ChatMessage], *, ctx: CondenseContext
    ) -> CondenserPipelineResult:
        current = list(messages)
        applied: list[dict[str, Any]] = []
        for condenser in self._condensers:
            if message_chars(current) <= ctx.target_chars:
                break
            if not condenser.applies(ctx):
                continue
            before = message_chars(current)
            result = await condenser.condense(current, ctx=ctx)
            if not result.changed:
                continue
            # Anti-thrash: reject an ineffective pass rather than accept churn.
            if result.chars_freed < int(self._minimum_progress * before):
                applied.append(
                    {"condenser": condenser.name, "rejected": "insufficient_progress"}
                )
                continue
            current = result.messages
            applied.append({"condenser": condenser.name, **result.audit})
        fit = message_chars(current) <= ctx.target_chars
        return CondenserPipelineResult(
            messages=current, applied=applied, fit=fit, exhausted=not fit
        )


__all__ = [
    "Condenser",
    "CondenseContext",
    "CondenseResult",
    "CondenserPipeline",
    "CondenserPipelineResult",
    "message_chars",
]
