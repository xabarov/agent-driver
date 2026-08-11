"""Merge / synthesize the results of a subagent group (coordination C1).

After a fan-out joins (:func:`run_subagent_group`), the parent has N child answers to
combine. The runtime carried the merge vocabulary (``SubagentMergeMode``) but only on
the model-planner path, and its ``SYNTHESIZE`` mode degraded to string concatenation.
This brings merge to the SDK primitive layer over the SDK's own ``SubagentResult``:

* :func:`merge_subagent_results` — deterministic, no LLM: ``APPEND`` (labeled
  concatenation), ``RANK`` (longest-answer first), ``VOTE`` (plurality answer),
  ``MANUAL`` (a review stub). ``SYNTHESIZE`` is rejected here — it needs a model.
* :func:`synthesize_subagent_results` — a *real* LLM synthesis of the child answers
  via one cache-safe aux call (the honest ``SYNTHESIZE``), degrading to ``APPEND``
  only if the model call fails.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Sequence
from typing import Any

from agent_driver.contracts.enums import RunStatus, SubagentMergeMode
from agent_driver.contracts.messages import ChatMessage
from agent_driver.sdk.subagent import SubagentResult

_SYNTHESIS_SYSTEM = (
    "You are given several independent answers to the SAME task, each from a "
    "different worker. Synthesize them into ONE coherent, non-redundant answer: keep "
    "what they agree on, reconcile or briefly note any real disagreement, and drop "
    "duplication. Do not invent facts none of them state. Reply with only the "
    "synthesized answer."
)


def _contributing(
    results: Sequence[SubagentResult | None], *, include_partial: bool
) -> list[tuple[str, str]]:
    """Return ``(label, answer)`` for children that contribute to the merge.

    Always includes ``COMPLETED`` children with a non-empty answer. When
    ``include_partial`` is set, also includes non-``COMPLETED`` children that still
    produced a non-empty answer, with the label marked ``(partial: <status>)`` so a
    salvaged answer is never mistaken for a finished one (C3).
    """
    pairs: list[tuple[str, str]] = []
    for item in results:
        if item is None:
            continue
        answer = (item.answer or "").strip()
        if not answer:
            continue
        if item.status == RunStatus.COMPLETED:
            pairs.append((item.agent_type, answer))
        elif include_partial:
            pairs.append((f"{item.agent_type} (partial: {item.status.value})", answer))
    return pairs


def _bounded(text: str, max_chars: int | None) -> str:
    if max_chars is not None and max_chars >= 0 and len(text) > max_chars:
        return text[:max_chars]
    return text


def merge_subagent_results(
    results: Sequence[SubagentResult | None],
    *,
    mode: SubagentMergeMode = SubagentMergeMode.APPEND,
    max_items: int | None = None,
    max_chars: int | None = None,
    label: bool = True,
    include_partial: bool = False,
) -> str:
    """Deterministically merge completed child answers into one string.

    ``APPEND`` concatenates (optionally ``label``-prefixed by ``agent_type``); ``RANK``
    orders by answer length (longest first) then concatenates; ``VOTE`` returns the
    plurality answer (ties → first seen); ``MANUAL`` returns a review stub. ``max_items``
    caps how many answers contribute; ``max_chars`` caps the result length. Only
    ``COMPLETED`` children with a non-empty answer contribute. ``SYNTHESIZE`` raises —
    use :func:`synthesize_subagent_results` (it needs a model).
    """
    if mode == SubagentMergeMode.SYNTHESIZE:
        raise ValueError(
            "SYNTHESIZE needs a model — use synthesize_subagent_results(...)"
        )
    pairs = _contributing(results, include_partial=include_partial)
    if mode == SubagentMergeMode.VOTE:
        if not pairs:
            return ""
        counts = Counter(answer for _, answer in pairs)
        # most_common is insertion-stable for ties → first-seen wins.
        return _bounded(counts.most_common(1)[0][0], max_chars)
    if mode == SubagentMergeMode.MANUAL:
        return f"manual review required: {len(pairs)} candidate answer(s)"
    if mode == SubagentMergeMode.RANK:
        pairs = sorted(pairs, key=lambda item: len(item[1]), reverse=True)
    if max_items is not None and max_items >= 0:
        pairs = pairs[:max_items]
    parts = [
        f"[{label_text}] {answer}" if label else answer for label_text, answer in pairs
    ]
    return _bounded("\n\n".join(parts), max_chars)


async def synthesize_subagent_results(
    results: Sequence[SubagentResult | None],
    *,
    provider: Any,
    model: str | None = None,
    instruction: str | None = None,
    include_partial: bool = False,
    max_input_chars: int = 8000,
) -> str:
    """LLM-synthesize completed child answers into one answer (the real ``SYNTHESIZE``).

    Runs one cache-safe aux completion over the labeled child answers. ``instruction``
    overrides the default synthesis system prompt. With zero/one contributing answer
    there is nothing to synthesize, so the lone answer (or ``""``) is returned without a
    model call. On any provider error it degrades to :func:`merge_subagent_results`
    (``APPEND``) rather than raising.
    """
    pairs = _contributing(results, include_partial=include_partial)
    if not pairs:
        return ""
    if len(pairs) == 1:
        return pairs[0][1]
    body = "\n\n".join(
        f"--- {label_text} ---\n{answer[:max_input_chars]}" for label_text, answer in pairs
    )
    try:
        from agent_driver.llm.aux import aux_completion

        response = await aux_completion(
            provider=provider,
            model=model,
            task="subagent_synthesis",
            temperature=0.0,
            messages=[
                ChatMessage(role="system", content=instruction or _SYNTHESIS_SYSTEM),
                ChatMessage(role="user", content=body[: max_input_chars * 4]),
            ],
        )
    except Exception:  # noqa: BLE001 - synthesis must not break the group merge
        return merge_subagent_results(results, mode=SubagentMergeMode.APPEND, include_partial=include_partial)
    text = (response.message.content or "").strip()
    return text or merge_subagent_results(results, mode=SubagentMergeMode.APPEND, include_partial=include_partial)


__all__ = ["merge_subagent_results", "synthesize_subagent_results"]
