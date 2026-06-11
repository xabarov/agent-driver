"""Deterministic planning-mode hints for adaptive planning."""

from __future__ import annotations

import re
from collections.abc import Callable, Sequence

from agent_driver.contracts.context import PlanningHint
from agent_driver.contracts.enums import PlanningHintLevel, SideEffectClass
from agent_driver.contracts.tools import ToolCall, ToolManifest, ToolPolicyInput

_IMPLEMENTATION_PATTERNS = (
    r"\b(add|build|create|implement|refactor|rewrite|change|modify|fix|debug|wire|integrate)\b",
    r"\b(feature|endpoint|api|runtime|policy|schema|migration|support|tests?)\b",
    r"\b(multi[- ]?file|architecture|architectural|design decision|side effect)\b",
    r"\b(добавь|создай|реализуй|обнови|измени|исправь|отрефактори|перепиши|интегрируй)\b",
    r"\b(фича|эндпоинт|ручк[аи]|политик[аи]|схем[ау]|миграци[яю]|тесты?)\b",
    r"\b(архитектур|много файлов|несколько файлов|побочн)\b",
)
_COMPLEXITY_PATTERNS = (
    r"\b(plan|roadmap|phase|phases|approach|trade[- ]?offs?|multiple options)\b",
    r"\b(план|роадмап|фаз[аы]|подход|вариант[ыов]|компромисс)\b",
)
_SIMPLE_PATTERNS = (
    r"\b(typo|spelling|rename only|one line|single line|quick answer)\b",
    r"\b(опечатк[ауи]|переименуй только|одна строка|быстрый ответ)\b",
)
_RESEARCH_PATTERNS = (
    r"\b(explain|research|compare|analyze|summarize|what is|how does)\b",
    r"\b(объясни|исследуй|сравни|проанализируй|резюмируй|что такое|как работает)\b",
)


def _matches_any(text: str, patterns: tuple[str, ...]) -> list[str]:
    return [pattern for pattern in patterns if re.search(pattern, text, re.IGNORECASE)]


def classify_planning_hint(
    message: str,
    *,
    side_effecting_tool_planned: bool = False,
    subagent_spawn_requested: bool = False,
    expected_steps: int | None = None,
) -> PlanningHint:
    """Classify whether a request should use planning mode.

    The classifier is intentionally conservative. It can suggest plan mode from
    the user's request, but only runtime-known safety boundaries return
    ``required``.
    """
    text = " ".join(message.strip().split())
    signals: list[str] = []
    if side_effecting_tool_planned:
        signals.append("side_effecting_tool_planned")
    if subagent_spawn_requested:
        signals.append("subagent_spawn_requested")
    if expected_steps is not None and expected_steps >= 4:
        signals.append("expected_steps_ge_4")
    if signals:
        return PlanningHint(
            level=PlanningHintLevel.REQUIRED,
            reason="runtime safety boundary requires approved planning",
            signals=signals,
        )

    if not text:
        return PlanningHint()

    simple = _matches_any(text, _SIMPLE_PATTERNS)
    research = _matches_any(text, _RESEARCH_PATTERNS)
    implementation = _matches_any(text, _IMPLEMENTATION_PATTERNS)
    complexity = _matches_any(text, _COMPLEXITY_PATTERNS)
    word_count = len(text.split())
    if simple and not complexity:
        return PlanningHint(
            level=PlanningHintLevel.NONE,
            reason="request looks simple and narrowly scoped",
            signals=["simple_scope"],
        )
    if implementation and (complexity or word_count >= 12 or len(implementation) >= 2):
        return PlanningHint(
            level=PlanningHintLevel.SUGGESTED,
            reason="request looks like non-trivial implementation work",
            signals=["implementation_request", *(["complexity_signal"] if complexity else [])],
        )
    if complexity and not research:
        return PlanningHint(
            level=PlanningHintLevel.SUGGESTED,
            reason="request asks for planning or approach selection",
            signals=["complexity_signal"],
        )
    if research and not implementation:
        return PlanningHint(
            level=PlanningHintLevel.NONE,
            reason="request looks research-only",
            signals=["research_only"],
        )
    return PlanningHint()


def classify_planning_hint_for_tool_calls(
    calls: Sequence[ToolCall],
    *,
    manifest_lookup: Callable[[str], ToolManifest | None],
    current_tool_calls: int = 0,
) -> PlanningHint:
    """Classify planning need from a planned tool batch."""
    side_effecting = False
    subagent_spawn = False
    for call in calls:
        if call.tool_name == "agent_tool":
            subagent_spawn = True
        manifest = manifest_lookup(call.tool_name)
        if manifest is None:
            continue
        if manifest.side_effect in {
            SideEffectClass.REVERSIBLE_WRITE,
            SideEffectClass.IRREVERSIBLE_WRITE,
            SideEffectClass.EXTERNAL_ACTION,
        }:
            side_effecting = True
    expected_steps = current_tool_calls + len(calls)
    return classify_planning_hint(
        "",
        side_effecting_tool_planned=side_effecting,
        subagent_spawn_requested=subagent_spawn,
        expected_steps=expected_steps if expected_steps >= 4 else None,
    )


def _hint_rank(level: str) -> int:
    if level == PlanningHintLevel.REQUIRED.value:
        return 2
    if level == PlanningHintLevel.SUGGESTED.value:
        return 1
    return 0


def tool_policy_with_planned_tool_hint(
    policy: ToolPolicyInput,
    calls: Sequence[ToolCall],
    *,
    manifest_lookup: Callable[[str], ToolManifest | None],
    current_tool_calls: int = 0,
) -> ToolPolicyInput:
    """Merge planned-tool planning hint into tool policy metadata.

    This only enriches metadata. It does not make hints enforceable unless the
    host explicitly sets ``planning_hint_enforce=true``.
    """
    hint = classify_planning_hint_for_tool_calls(
        calls,
        manifest_lookup=manifest_lookup,
        current_tool_calls=current_tool_calls,
    )
    if hint.level == PlanningHintLevel.NONE:
        return policy
    metadata = dict(policy.metadata)
    existing = metadata.get("planning_hint")
    existing_level = (
        str(existing.get("level") or "")
        if isinstance(existing, dict)
        else PlanningHintLevel.NONE.value
    )
    if _hint_rank(existing_level) >= _hint_rank(hint.level.value):
        return policy
    metadata["planning_hint"] = hint.model_dump(mode="json")
    return policy.model_copy(update={"metadata": metadata})


__all__ = [
    "classify_planning_hint",
    "classify_planning_hint_for_tool_calls",
    "tool_policy_with_planned_tool_hint",
]
