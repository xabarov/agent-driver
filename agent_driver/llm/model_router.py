"""Pluggable per-request model routing (R-track R6).

A ``ModelRouter`` inspects a request and returns the ``model_role`` to use, so the runtime
can pick a cheaper or stronger model per turn — by difficulty, content, or any signal.
The chosen role then flows through the existing resolution: R2's ``model_role_map``
(role → model) and, at call time, R3's ``role_providers`` (role → provider). A router
therefore *composes* with the registries instead of duplicating them — it only decides
*which role*, never wires models/providers itself.

Opt-in: with no router configured the run's static ``model_role`` is used, unchanged. An
unmapped routed role falls back to the provider default (a safe no-op), so a router can be
enabled before the registries are populated — it will show up in traces first, then start
changing models once ``model_role_map`` / ``role_providers`` gain the roles it emits.

Taxonomy (see the R6 survey): this is the *difficulty-router* shape — one model chosen
up-front per request. The *cascade* (cheap-first, escalate on low confidence) and
*draft-then-verify* (two models on one request) shapes change the step loop's control flow
and are a separate follow-on.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol, runtime_checkable

from agent_driver.contracts.runtime import AgentRunInput


@runtime_checkable
class ModelRouter(Protocol):
    """Decides the ``model_role`` for one request."""

    def route(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        run_input: AgentRunInput,
        default_role: str,
    ) -> str:
        """Return the ``model_role`` to use for this request (may be ``default_role``)."""
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

    def route(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        run_input: AgentRunInput,
        default_role: str,
    ) -> str:
        text = last_user_text(messages)
        if not text.strip():
            return default_role  # nothing to judge → leave the run's role
        lowered = text.lower()
        if any(keyword in lowered for keyword in self.strong_keywords):
            return self.strong_role
        if "```" in text:  # a fenced code block ⇒ treat as hard
            return self.strong_role
        if len(text) > self.simple_max_chars or len(text.split()) > self.simple_max_words:
            return self.strong_role
        return self.simple_role


__all__ = [
    "HeuristicDifficultyRouter",
    "ModelRouter",
    "last_user_text",
]
