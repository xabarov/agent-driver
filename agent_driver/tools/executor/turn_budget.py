"""Per-turn aggregate tool-output budget (epic 033 phase B, tier 3).

Tiers 1 (per-tool ``output_char_budget`` pre-truncation) and 2 (per-result spill
to an ArtifactStore over ``max_result_size_chars``) already cap a *single* tool
result. Tier 3 is the hermes ``enforce_turn_budget`` backstop: when MANY
medium-sized results in one assistant turn combine to overflow context, the
largest un-truncated summaries are trimmed (newest-safe) until the turn's
aggregate is back under budget.

Unlike hermes (which spills to disk), this pure pass truncates lossily via the
epic-029 ``safe_preview`` primitive (codepoint-safe + explicit «…опущено N…»
marker) so it works even for a host with no ArtifactStore — the common case
(MeetScript). Per-result store spill (tier 2) still runs first for hosts that
wire a store, so tier 3 only ever fires on the residual aggregate. Default off
(``budget_chars`` None/0) — a no-op that preserves historical behaviour.
"""

from __future__ import annotations

import json
from typing import Any

from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.tools.tool_result_preview import safe_preview

_TRUNCATED_META_KEY = "turn_budget_truncated"


def _envelope_output_size(envelope: ToolResultEnvelope) -> int:
    """Chars the envelope contributes to the model observation (summary + structured)."""
    size = len(envelope.summary or "")
    if envelope.structured_output:
        try:
            size += len(json.dumps(envelope.structured_output, default=str))
        except (TypeError, ValueError):
            size += len(str(envelope.structured_output))
    return size


def enforce_turn_output_budget(
    envelopes: list[ToolResultEnvelope],
    *,
    budget_chars: int | None,
    preview_chars: int = 2000,
) -> tuple[list[ToolResultEnvelope], dict[str, Any]]:
    """Trim the largest summaries until the turn aggregate fits ``budget_chars``.

    Returns ``(envelopes, audit)``. ``audit`` is raw-free counts. A no-op (returns
    the input list unchanged) when ``budget_chars`` is falsy or the aggregate is
    already under budget. Only trims a summary longer than ``preview_chars`` (a
    shorter one can't recover meaningful space) and never re-trims one already
    marked, so repeated passes converge.
    """
    if not budget_chars or budget_chars <= 0:
        return envelopes, {"activated": False}
    sizes = [_envelope_output_size(e) for e in envelopes]
    total = sum(sizes)
    if total <= budget_chars:
        return envelopes, {"activated": False, "total_chars": total}

    result = list(envelopes)
    order = sorted(range(len(result)), key=lambda i: sizes[i], reverse=True)
    spilled = 0
    saved = 0
    for i in order:
        if total <= budget_chars:
            break
        env = result[i]
        summary = env.summary or ""
        if env.metadata.get(_TRUNCATED_META_KEY) or len(summary) <= preview_chars:
            continue
        trimmed = safe_preview(summary, max_chars=preview_chars)
        delta = len(summary) - len(trimmed)
        if delta <= 0:
            continue
        result[i] = env.model_copy(
            update={
                "summary": trimmed,
                "truncated": True,
                "metadata": {**env.metadata, _TRUNCATED_META_KEY: True},
            }
        )
        total -= delta
        saved += delta
        spilled += 1
    return result, {
        "activated": spilled > 0,
        "spilled_count": spilled,
        "chars_saved": saved,
        "total_chars_after": total,
    }


__all__ = ["enforce_turn_output_budget"]
