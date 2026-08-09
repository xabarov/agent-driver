"""Pluggable per-request model routing (R-track R6/R5).

A ``ModelRouter`` inspects a request and returns the ``model_role`` to use, so the runtime
can pick a cheaper or stronger model per turn — by request difficulty (R6), by run phase
(R5 opusplan: strong planner, cheap executor), or any signal. The chosen role then flows
through the existing resolution: R2's ``model_role_map`` (role → model) and, at call time,
R3's ``role_providers`` (role → provider). A router therefore *composes* with the
registries instead of duplicating them — it only decides *which role*, never wires
models/providers itself.

Opt-in: with no router configured the run's static ``model_role`` is used, unchanged. An
unmapped routed role falls back to the provider default (a safe no-op), so a router can be
enabled before the registries are populated — it will show up in traces first, then start
changing models once ``model_role_map`` / ``role_providers`` gain the roles it emits.

Routers receive a :class:`RouteContext` (not loose kwargs) so new routing signals can be
added without changing the protocol.

Taxonomy (see the R6 survey): these are the *difficulty-router* / *phase-router* shapes —
one model chosen up-front per request. The *cascade* (cheap-first, escalate on low
confidence) and *draft-then-verify* (two models on one request) shapes change the step
loop's control flow and are a separate follow-on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput


@dataclass(frozen=True, slots=True)
class RouteContext:
    """What a :class:`ModelRouter` sees when choosing a role for one request.

    Extensible: new signals (e.g. remaining cost budget for a cost-aware router) are added
    here without changing the protocol.
    """

    messages: Sequence[Mapping[str, Any]]
    run_input: AgentRunInput
    default_role: str
    # Completed LLM iterations so far in this run; 0 = the first (planning) turn. Enables
    # phase-based routing (R5 PlanExecuteRouter).
    step_index: int = 0


@runtime_checkable
class ModelRouter(Protocol):
    """Decides the ``model_role`` for one request (synchronously, no extra LLM call)."""

    def route(self, ctx: RouteContext) -> str:
        """Return the ``model_role`` to use for this request (may be ``ctx.default_role``)."""
        ...


@runtime_checkable
class AsyncModelRouter(Protocol):
    """A router that needs to ``await`` (e.g. a small LLM classifies difficulty).

    Resolved in the async step loop BEFORE the request is built, not in the synchronous
    build path — a router with an ``aroute`` method is driven there and its verdict is
    reused for the rest of the run. (Duck-typed on the ``aroute`` name, so an impl can be
    both a sync and async router if it wants.)
    """

    async def aroute(self, ctx: RouteContext) -> str:
        """Return the ``model_role`` for this request (may be ``ctx.default_role``)."""
        ...


def last_user_text(messages: Sequence[Mapping[str, Any]]) -> str:
    """The content of the last ``user`` message (empty string if none)."""
    for message in reversed(list(messages)):
        if str(message.get("role", "")) == "user":
            content = message.get("content")
            if isinstance(content, str):
                return content
    return ""


# Words that signal a hard/reasoning-heavy turn (reference: openclaude smartModelRouting
# STRONG_KEYWORDS). Substring match, case-insensitive.
_DEFAULT_STRONG_KEYWORDS: tuple[str, ...] = (
    "plan",
    "design",
    "architect",
    "refactor",
    "debug",
    "prove",
    "derive",
    "analyze",
    "analyse",
    "optimize",
    "optimise",
    "root cause",
    "trade-off",
    "tradeoff",
    "step by step",
    "reason about",
)


class HeuristicDifficultyRouter:
    """Route each turn to a ``simple`` or ``strong`` role by a cheap keyword/length
    heuristic — no extra LLM call. A short, keyword-free turn goes to ``simple_role``
    (cheap/fast); anything with a strong keyword, a fenced code block, or over the
    length thresholds goes to ``strong_role``.

    The emitted roles are just labels — map them in ``RunnerConfig(model_role_map=...)``
    and/or ``role_providers=...`` to actual models/providers. Reference: openclaude's
    per-turn simple-vs-strong classifier.
    """

    def __init__(
        self,
        *,
        simple_role: str = "simple",
        strong_role: str = "strong",
        strong_keywords: Sequence[str] = _DEFAULT_STRONG_KEYWORDS,
        simple_max_chars: int = 280,
        simple_max_words: int = 40,
    ) -> None:
        self.simple_role = simple_role
        self.strong_role = strong_role
        self.strong_keywords = tuple(k.lower() for k in strong_keywords)
        self.simple_max_chars = simple_max_chars
        self.simple_max_words = simple_max_words

    def route(self, ctx: RouteContext) -> str:
        text = last_user_text(ctx.messages)
        if not text.strip():
            return ctx.default_role  # nothing to judge → leave the run's role
        lowered = text.lower()
        if any(keyword in lowered for keyword in self.strong_keywords):
            return self.strong_role
        if "```" in text:  # a fenced code block ⇒ treat as hard
            return self.strong_role
        if len(text) > self.simple_max_chars or len(text.split()) > self.simple_max_words:
            return self.strong_role
        return self.simple_role


class PlanExecuteRouter:
    """Opusplan-style phase split (R5): the run's first ``plan_steps`` turns (planning /
    decomposition) route to a strong ``planner_role``; every later turn routes to a
    cheaper ``executor_role``. A strong model reasons about *what* to do, then a cheap
    model carries it out — the single-run orchestrator-worker split.

    Reference: Claude Code ``opusplan`` (Opus plans, Sonnet executes); Anthropic's
    orchestrator-worker research system. Map ``planner_role`` / ``executor_role`` in the
    parent's ``model_role_map`` / ``role_providers``.
    """

    def __init__(
        self,
        *,
        planner_role: str = "planner",
        executor_role: str = "executor",
        plan_steps: int = 1,
    ) -> None:
        self.planner_role = planner_role
        self.executor_role = executor_role
        self.plan_steps = max(0, plan_steps)

    def route(self, ctx: RouteContext) -> str:
        return (
            self.planner_role
            if ctx.step_index < self.plan_steps
            else self.executor_role
        )


_LLM_ROUTER_SYSTEM = (
    "You are a routing classifier. Read the user's request and reply with EXACTLY one "
    "word — STRONG or SIMPLE — nothing else.\n"
    "STRONG: needs multi-step reasoning, planning, decomposition, analysis, design, "
    "refactoring, debugging, math/derivation, or weighing trade-offs.\n"
    "SIMPLE: a lookup, a short factual question, a count, a rename/format, or a trivial "
    "one-step edit."
)


class LlmDifficultyRouter:
    """Async router — a small, fast model classifies the user's request as simple/strong.

    Classified ONCE per run (a run's difficulty is set by its opening question), so the
    "router tax" is a single cheap call, not one per turn; the verdict is cached by the
    step loop and reused for every inner tool-loop turn. On any error it falls back to a
    heuristic router — routing must never break a run.

    Point ``model`` at a small low-latency model (e.g. a *-flash-lite / *-nano / a 3–4B):
    the classifier emits one word, so latency dominates and a tiny model is both cheapest
    and fastest. ``provider`` is any ``LlmProvider`` that serves that model. The emitted
    roles resolve through the usual ``model_role_map`` (R2) / ``role_providers`` (R3).
    """

    def __init__(
        self,
        *,
        provider: Any,
        model: str | None = None,
        simple_role: str = "simple",
        strong_role: str = "strong",
        fallback: ModelRouter | None = None,
        max_input_chars: int = 2000,
    ) -> None:
        self._provider = provider
        self._model = model
        self.simple_role = simple_role
        self.strong_role = strong_role
        self._fallback = fallback or HeuristicDifficultyRouter(
            simple_role=simple_role, strong_role=strong_role
        )
        self._max_input_chars = max_input_chars

    async def aroute(self, ctx: RouteContext) -> str:
        text = last_user_text(ctx.messages)
        if not text.strip():
            return ctx.default_role
        try:
            from agent_driver.llm.aux import aux_completion

            resp = await aux_completion(
                provider=self._provider,
                model=self._model,
                task="model_router",
                temperature=0.0,
                max_tokens=4,
                messages=[
                    ChatMessage(role="system", content=_LLM_ROUTER_SYSTEM),
                    ChatMessage(role="user", content=text[: self._max_input_chars]),
                ],
            )
            verdict = (resp.message.content or "").strip().lower()
        except Exception:  # noqa: BLE001 — a routing call must never break the run
            return self._fallback.route(ctx)
        if "strong" in verdict:
            return self.strong_role
        if "simple" in verdict:
            return self.simple_role
        return self._fallback.route(ctx)  # unparseable → heuristic


__all__ = [
    "AsyncModelRouter",
    "HeuristicDifficultyRouter",
    "LlmDifficultyRouter",
    "ModelRouter",
    "PlanExecuteRouter",
    "RouteContext",
    "last_user_text",
]
