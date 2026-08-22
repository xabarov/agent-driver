"""Pure computation helpers for compaction (extracted from compaction_stage).

Leaf module: material-unit protection, the window-scaled char cap, cost
accounting, the LLM-full excerpt builder, backend resolution, and small
payload/summary helpers. Depends only on contracts/context primitives — never
on the compaction dispatch or the ``CompactionStageHost`` protocol (host params
are typed ``Any`` here), so the import edge to ``compaction_stage`` stays one-way.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from agent_driver.context import (
    ptl_retry_drop_oldest_groups,
    sanitize_compaction_text,
)
from agent_driver.contracts.context.run_budget import (
    COMPACTION_WINDOW_CHAR_FRACTION as COMPACTION_INPUT_WINDOW_FRACTION,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.scaffolding import is_scaffolding
from agent_driver.runtime.metadata_state import get_cost_runtime_state
from agent_driver.runtime.single_agent.types import (
    RunContext,
)

# Rough char↔token ratio for turning the token window into a char budget. A single
# constant here (BUG-6 will replace all such 4s with a real/provider tokenizer).
_COMPACTION_CHARS_PER_TOKEN = 4
# Absolute memory backstop only (not a window-relative cap): guards against a
# pathologically-large configured window feeding an unbounded excerpt. Large enough
# to never be the effective cap for a realistic model.
_MAX_SCALED_COMPACTION_CHARS = 4_000_000
#: ``CompactionSettings.compaction_model`` default — a sentinel meaning "use the
#: run's own model", NOT a literal model id. Sending it to a provider is a
#: guaranteed invalid-model error, so it must resolve to the main completion's model
#: (``request.model``, which may itself be ``None`` → the provider's configured
#: default) exactly as the primary LLM call does.
_DEFAULT_COMPACTION_MODEL_SENTINEL = "default"


def _message_material_unit_hashes(message: Any) -> set[str]:
    """Return bounded host-supplied identities without reading raw evidence."""
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    raw = metadata.get("material_unit_hashes")
    if not isinstance(raw, list):
        return set()
    return {
        value.strip()
        for item in raw
        if isinstance(item, str) and (value := item.strip()) and len(value) <= 128
    }


def _material_unit_receipt(
    *,
    original_messages: list[Any],
    retained_messages: list[Any],
    pre_summary_groups_dropped: bool,
) -> dict[str, list[str]]:
    """Partition material identities across retained/compacted/omitted paths."""
    original = (
        set().union(
            *(_message_material_unit_hashes(message) for message in original_messages)
        )
        if original_messages
        else set()
    )
    retained = (
        set().union(
            *(_message_material_unit_hashes(message) for message in retained_messages)
        )
        if retained_messages
        else set()
    )
    unresolved = original - retained
    return {
        "retained_unit_hashes": sorted(retained),
        "compacted_unit_hashes": (
            [] if pre_summary_groups_dropped else sorted(unresolved)
        ),
        "omitted_unit_hashes": (
            sorted(unresolved) if pre_summary_groups_dropped else []
        ),
    }


def _is_protected_message(message: Any, *, is_last: bool) -> bool:
    """A message the summariser must never drop or evict.

    The single protection predicate shared by the compaction excerpt (which groups
    it may not PTL-drop) and the post-summary retention set (which messages survive
    the rewrite). Previously these two disagreed — the retention set omitted
    ``compaction_evidence`` / ``material_unit_hashes``, so a message flagged solely
    with those was fed to the summariser and then silently dropped (data loss).
    """
    if is_last:
        return True
    role = str(getattr(message.role, "value", message.role) or "").casefold()
    if role == "system":
        return True
    metadata = message.metadata if isinstance(message.metadata, dict) else {}
    return bool(
        metadata.get("compaction_protected")
        or metadata.get("compaction_evidence")
        or metadata.get("material_fact_ids")
        or _message_material_unit_hashes(message)
    )


def _retained_messages_after_full_compaction(messages: list[Any]) -> list[Any]:
    """Keep stable contracts and the live instruction after successful summary."""
    if not messages:
        return []
    last_index = len(messages) - 1
    return [
        message
        for index, message in enumerate(messages)
        if _is_protected_message(message, is_last=index == last_index)
    ]


def _scaled_context_char_cap(
    host: Any,
    *,
    context: RunContext,
    base_max_chars: int,
) -> tuple[int, str]:
    """Scale a compaction-related cap from the resolved public run budget."""
    raw = context.metadata.get("effective_context_budget")
    if not isinstance(raw, dict):
        return base_max_chars, "runner_config"
    resolved_compaction = raw.get("max_compaction_chars")
    source = str(raw.get("source") or "runner_config")
    if not isinstance(resolved_compaction, int) or resolved_compaction < 1:
        return base_max_chars, "runner_config"
    baseline = max(1, int(host._config.ptl_retry_max_chars))
    scaled = (base_max_chars * resolved_compaction + baseline - 1) // baseline
    # BUG-5: the compaction char budget derives from the resolved MODEL window, NOT
    # budget.max_chars — that field is the deterministic-TRIMMING budget (default
    # ~6000) and would otherwise clamp the summariser to a sliver of history on a
    # large-context model. Falls back to max_chars only when the window is unknown.
    window_tokens = raw.get("context_window_estimate")
    output_reserve = raw.get("output_token_reserve") or 0
    if isinstance(window_tokens, int) and window_tokens > 0:
        window_chars = (
            max(1, window_tokens - int(output_reserve)) * _COMPACTION_CHARS_PER_TOKEN
        )
    else:
        max_chars = raw.get("max_chars")
        window_chars = (
            int(max_chars) if isinstance(max_chars, int) and max_chars > 0 else scaled
        )
    # BUG-1: cost-cap the summariser input at a FRACTION of the window char budget,
    # not a fixed 262144 (which bound below the window on large-context models). The
    # absolute backstop remains only as a memory guard, never the effective cap.
    input_cap = max(
        base_max_chars, int(window_chars * COMPACTION_INPUT_WINDOW_FRACTION)
    )
    cap = min(
        max(base_max_chars, scaled),
        input_cap,
        _MAX_SCALED_COMPACTION_CHARS,
    )
    return cap, source


def _account_compaction_cost(
    context: RunContext, compaction_result: Any, *, provider: Any
) -> None:
    """Accumulate a compaction call's usage into the run cost ledger.

    Tagged by the compaction model's own name, so auxiliary-model spend (E1) is
    separated from the main model's in the ledger rollup. Accounts even failed
    attempts — they still consumed tokens.
    """
    if compaction_result is None:
        return
    input_tokens = int(getattr(compaction_result, "input_tokens_estimate", 0) or 0)
    output_tokens = int(getattr(compaction_result, "output_tokens_estimate", 0) or 0)
    if input_tokens == 0 and output_tokens == 0:
        return
    from agent_driver.contracts.usage import (  # pylint: disable=import-outside-toplevel
        UsageSummary,
    )

    get_cost_runtime_state(context).accumulate(
        UsageSummary(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            total_tokens=input_tokens + output_tokens,
            model_provider=getattr(provider, "name", "auxiliary"),
            model_name=str(getattr(compaction_result, "model", "") or "compaction"),
        )
    )


def _has_sendable_content(messages: list[ChatMessage]) -> bool:
    """True when the compacted set carries at least one non-system message with content.

    Providers reject prompts whose payload is empty or system-only («Input required»);
    such a set must never replace a working prompt.
    """

    def _role_value(message: ChatMessage) -> str:
        role = getattr(message, "role", "")
        return str(getattr(role, "value", role))

    return any(
        _role_value(message) != "system"
        and (getattr(message, "content", "") or "").strip()
        for message in messages
    )


def _pipeline_target_chars(host: Any, *, context: RunContext) -> int:
    """Char budget the compacted View should fit under — the model-window fraction."""
    cap, _source = _scaled_context_char_cap(
        host, context=context, base_max_chars=int(host._config.ptl_retry_max_chars)
    )
    return max(1, cap)


def _effective_window_tokens(context: RunContext) -> int:
    """Resolved model window in tokens for tier sizing (fallback 128k)."""
    raw = context.metadata.get("effective_context_budget")
    if isinstance(raw, dict):
        window = raw.get("context_window_estimate")
        if isinstance(window, int) and window > 0:
            return window
    return 128_000


def _applied_tier_names(applied: list[dict[str, Any]]) -> list[str]:
    return [
        str(entry["condenser"])
        for entry in applied
        if isinstance(entry, dict)
        and entry.get("condenser")
        and "rejected" not in entry
    ]


@dataclass(slots=True)
class _FullCompactionExcerpt:
    """The prepared LLM-full-compaction excerpt plus the PTL-retry accounting the
    success receipt needs."""

    sanitized_excerpt: str
    max_chars: int
    budget_source: str
    kept_groups: list[str]
    dropped_groups: list[str]
    protected_indexes: set[int]
    #: Total non-scaffolding source groups in the full message list (before any
    #: ``skip_leading_groups`` rolling cursor). The rolling summary advances its
    #: ``rolling_summary_covers_upto`` cursor to this after folding a slice.
    total_source_groups: int = 0


def _build_full_compaction_excerpt(
    host: Any,
    *,
    context: RunContext,
    request: Any,
    skip_leading_groups: int = 0,
) -> _FullCompactionExcerpt:
    """Flatten non-scaffolding, non-empty message content into groups, mark the
    protected ones, apply the PTL-retry oldest-drop within the scaled char budget,
    and return the sanitized excerpt plus the drop/keep accounting.

    Epic 043 C: runtime scaffolding turns (nudges, recovery hints) are dropped —
    they are ephemeral and role is flattened away here, so the summary model would
    otherwise be free to read a nudge as user intent.

    Option B2: ``skip_leading_groups`` drops the first N source groups the rolling
    summary already absorbed, so only the newly-overflowed slice is summarised;
    ``total_source_groups`` on the result is the cursor to persist after folding.
    """
    source_groups: list[str] = []
    source_protected: set[int] = set()
    last_message_index = len(request.messages) - 1
    for message_index, message in enumerate(request.messages):
        if is_scaffolding(message):
            continue
        content = str(message.content or "")
        if not content.strip():
            continue
        group_index = len(source_groups)
        source_groups.append(content)
        if _is_protected_message(message, is_last=message_index == last_message_index):
            source_protected.add(group_index)
    total_source_groups = len(source_groups)
    # Rolling cursor: keep only groups past what the prior summary already covers,
    # re-indexing protection onto the remaining slice.
    skip = max(0, min(skip_leading_groups, total_source_groups))
    raw_groups = source_groups[skip:]
    protected_indexes = {i - skip for i in source_protected if i >= skip}
    kept_groups = list(raw_groups)
    dropped_groups: list[str] = []
    effective_ptl_max_chars, budget_source = _scaled_context_char_cap(
        host,
        context=context,
        base_max_chars=host._config.ptl_retry_max_chars,
    )
    if host._config.enable_ptl_retry:
        kept_groups, dropped_groups = ptl_retry_drop_oldest_groups(
            groups=raw_groups,
            max_chars=effective_ptl_max_chars,
            protected_indexes=protected_indexes,
        )
    sanitized_excerpt = sanitize_compaction_text("\n".join(kept_groups))
    return _FullCompactionExcerpt(
        sanitized_excerpt=sanitized_excerpt,
        max_chars=effective_ptl_max_chars,
        budget_source=budget_source,
        kept_groups=kept_groups,
        dropped_groups=dropped_groups,
        protected_indexes=protected_indexes,
        total_source_groups=total_source_groups,
    )


def _resolve_compaction_backend(
    host: Any, *, request: Any = None
) -> tuple[Any, str | None]:
    """Resolve the (provider, model) for the compaction side task.

    E1: route to the auxiliary (cheaper) provider/model when configured, else the
    main provider + compaction_model. Epic 034: the model resolves through the
    per-task aux registry (``aux_model_for("compaction")`` → auxiliary_model →
    compaction_model) so the side task shares the one aux-backend seam.

    When no aux override is configured and ``compaction_model`` is left at its
    ``"default"`` sentinel, fall back to the run's own model (``request.model``)
    rather than sending the literal string ``"default"`` — a host that enables
    llm_compaction without explicitly naming a model would otherwise 400 on the
    first compaction. ``request.model`` may be ``None``, which the provider reads
    as its configured default, mirroring the primary completion.
    """
    provider = host._config.auxiliary_provider or host._deps.provider
    model = host._config.aux_model_for("compaction") or host._config.compaction_model
    if not model or model == _DEFAULT_COMPACTION_MODEL_SENTINEL:
        model = getattr(request, "model", None) if request is not None else None
    return provider, model


def _splice_summary_message(
    retained_messages: list[ChatMessage], summary: Any
) -> list[ChatMessage]:
    """Insert the compacted-summary system message ahead of the first non-system
    retained message (keeping any leading system prompt first)."""
    summary_text = json.dumps(
        summary,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    summary_message = ChatMessage.model_validate(
        {
            "role": "system",
            "content": f"Compacted summary:\n{summary_text}",
            "metadata": {"compaction_summary": True},
        }
    )
    first_non_system = next(
        (
            index
            for index, message in enumerate(retained_messages)
            if str(getattr(message.role, "value", message.role) or "").casefold()
            != "system"
        ),
        len(retained_messages),
    )
    return [
        *retained_messages[:first_non_system],
        summary_message,
        *retained_messages[first_non_system:],
    ]


def _result_from_payload(payload: dict[str, Any]):
    from agent_driver.contracts import CompactionMode, CompactionResult

    mode_raw = str(payload.get("mode", "none"))
    try:
        mode = CompactionMode(mode_raw)
    except ValueError:
        mode = CompactionMode.NONE
    return CompactionResult(
        compaction_id=str(payload.get("compaction_id", "cmp_unknown")),
        mode=mode,
        success=bool(payload.get("success", False)),
        metadata=(
            payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        ),
        retained_digest_ids=[
            str(item) for item in payload.get("retained_digest_ids", []) if item
        ],
        retained_artifact_ids=[
            str(item) for item in payload.get("retained_artifact_ids", []) if item
        ],
    )
