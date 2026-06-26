"""Retrieval priming for deferred tools.

Deferred tools (``manifest.should_defer``) are omitted from the LLM schema list
to keep niche/bulky tool sets out of every prompt. The discovery path is the
``tool_search`` tool — but that only pays off when the model actually *calls*
it, and weaker models often don't. When a deferred-but-needed tool never gets
surfaced, the model silently loses that capability.

A **defer primer** closes that gap without depending on the model: before each
LLM step the runtime scores the deferred tools against the live conversation and
surfaces the most relevant ones into the schema list directly (via the existing
explicit-allow path in ``_request_tools_from_registry``). The long tail stays
deferred; ``tool_search`` remains as a backstop for whatever the primer misses.

The primer is a pluggable ``Callable`` on ``RunnerConfig.defer_primer`` (default
``None`` → unchanged behaviour). ``keyword_relevance_primer`` is a generic,
language-agnostic default; a domain consumer (e.g. a non-English app) can supply
a smarter primer — synonym map, embeddings — without touching the runtime.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from agent_driver.contracts.tools import ToolManifest

__all__ = [
    "DeferPrimer",
    "DeferPrimerInput",
    "keyword_relevance_primer",
]


@dataclass(frozen=True, slots=True)
class DeferPrimerInput:
    """Inputs handed to a defer primer for one LLM step.

    ``conversation_text`` is the concatenated recent conversation (user +
    assistant + tool content) used for relevance matching only — it is never
    sent to the model. ``deferred`` is the set of currently-deferred,
    otherwise-permitted tool manifests the primer may choose to surface.
    """

    conversation_text: str
    deferred: tuple[ToolManifest, ...]


# A primer maps the step context to the names of deferred tools to surface this
# step. Returning an empty iterable surfaces nothing (pure ``tool_search``
# behaviour). Unknown / non-deferred names in the result are ignored downstream.
DeferPrimer = Callable[[DeferPrimerInput], Iterable[str]]


_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)
# Generic filler tokens that carry no tool-selection signal. Kept deliberately
# tiny + English-only: the primer must stay domain-/language-neutral, so we trim
# obvious noise rather than attempt real stop-word coverage.
_NOISE_TOKENS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "to",
        "of",
        "in",
        "on",
        "for",
        "with",
        "is",
        "are",
        "be",
        "tool",
        "use",
        "data",
    }
)


def _tokenize(text: str) -> set[str]:
    return {tok for tok in _TOKEN_RE.findall(text.lower()) if len(tok) > 1}


def keyword_relevance_primer(
    *,
    max_tools: int = 3,
    min_overlap: int = 1,
) -> DeferPrimer:
    """Build a generic keyword-relevance defer primer.

    Two signals, both language-agnostic:

    * **Name mention (strong).** If a deferred tool's exact registered name
      appears verbatim in the conversation, surface it. Models routinely name
      a tool they remember from an earlier turn even when its schema is absent
      ("I'll build the chart with ``chart_vegalite``") — a high-precision cue.
    * **Token overlap (weak).** Otherwise, score by how many meaningful tokens
      the tool's name + description share with the conversation, and surface
      the top ``max_tools`` that clear ``min_overlap``.

    Returns at most ``max_tools`` names. Name-mention hits always win the ranking
    over overlap-only hits.
    """

    def _primer(payload: DeferPrimerInput) -> list[str]:
        if not payload.deferred:
            return []
        text_lower = payload.conversation_text.lower()
        convo_tokens = _tokenize(payload.conversation_text) - _NOISE_TOKENS
        if not text_lower.strip():
            return []
        scored: list[tuple[int, int, str]] = []
        for manifest in payload.deferred:
            name = manifest.name
            mentioned = 1 if name and name.lower() in text_lower else 0
            terms = _tokenize(f"{name} {manifest.description}") - _NOISE_TOKENS
            overlap = len(terms & convo_tokens)
            if not mentioned and overlap < min_overlap:
                continue
            # Sort key: name-mention first, then overlap, then name for stability.
            scored.append((mentioned, overlap, name))
        scored.sort(key=lambda row: (-row[0], -row[1], row[2]))
        return [name for _, _, name in scored[:max_tools]]

    return _primer


def surfaced_deferred_tool_names(
    deferred: Sequence[ToolManifest],
    conversation_text: str,
    primer: DeferPrimer | None,
) -> tuple[str, ...]:
    """Run ``primer`` over the deferred manifests and return ordered names.

    Returns an empty tuple when there is no primer or no deferred tool. Result
    names are de-duplicated while preserving the primer's ordering; names that
    don't correspond to a supplied deferred manifest are dropped (the primer is
    advisory, the manifest set is authoritative).
    """
    if primer is None or not deferred:
        return ()
    valid = {manifest.name for manifest in deferred}
    seen: set[str] = set()
    ordered: list[str] = []
    for name in primer(
        DeferPrimerInput(
            conversation_text=conversation_text,
            deferred=tuple(deferred),
        )
    ):
        if name in valid and name not in seen:
            seen.add(name)
            ordered.append(name)
    return tuple(ordered)
