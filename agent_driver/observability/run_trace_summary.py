"""Summarize one chat run into scenario-checkable quality signals."""

from __future__ import annotations

from typing import Any

from agent_driver.runtime.planning_check import PLANNING_TOOL_NAMES
from agent_driver.runtime.single_agent.continuation import analyze_continuation_intent

_RESEARCH_TOOLS = frozenset({"web_search", "web_fetch"})
_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_cancelled"})


def summarize_run_trace(
    *,
    run_id: str,
    events: list[dict[str, object]],
    user_prompt: str | None = None,
    assistant_text: str | None = None,
    task_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact verdicts for live chat scenario debugging."""
    tool_names = _tool_names(events)
    terminal_event = _last_event_name(events, _TERMINAL_EVENTS)
    interrupt_reasons = _interrupt_reasons(events)
    text = assistant_text if assistant_text is not None else _assistant_text(events)
    continuation = analyze_continuation_intent(text)
    requires_research = _requires_research(
        task_contract=task_contract,
        user_prompt=user_prompt,
    )
    planning = _planning_summary(events, tool_names)

    failures: dict[str, bool] = {
        "stuck_on_interrupt": bool(interrupt_reasons) and terminal_event is None,
        "missing_terminal_event": terminal_event is None,
        "run_failed_or_cancelled": terminal_event in {"run_failed", "run_cancelled"},
        "missing_required_research_evidence": (
            requires_research
            and not any(name in _RESEARCH_TOOLS for name in tool_names)
        ),
        "progress_only_final": continuation.reason == "continuation_signal",
        "text_form_tool_call": continuation.reason == "text_form_tool_call",
        "fabricated_planning": planning["verdict"] == "fabricated"
        and _planning_execution_expected(
            requires_research=requires_research,
            user_prompt=user_prompt,
            assistant_text=text,
        ),
    }
    notes = _notes(
        failures=failures,
        continuation_reason=continuation.reason,
        interrupt_reasons=interrupt_reasons,
    )
    return {
        "run_id": run_id,
        "verdict": "fail" if any(failures.values()) else "pass",
        "terminal_event": terminal_event,
        "llm_calls": _count_events(events, "llm_call_completed"),
        "tool_calls": len(tool_names),
        "tool_names": tool_names,
        "research": {
            "required": requires_research,
            "tools_used": [name for name in tool_names if name in _RESEARCH_TOOLS],
        },
        "planning": planning,
        "interrupts": interrupt_reasons,
        "continuation_reason": continuation.reason,
        "failures": failures,
        "notes": notes,
    }


def _count_events(events: list[dict[str, object]], event_name: str) -> int:
    return sum(1 for event in events if event.get("event") == event_name)


def _last_event_name(
    events: list[dict[str, object]],
    names: frozenset[str],
) -> str | None:
    for event in reversed(events):
        name = event.get("event")
        if isinstance(name, str) and name in names:
            return name
    return None


def _event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _tool_names(events: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for event in events:
        name = event.get("event")
        if name not in {"tool_call_started", "tool_call_completed"}:
            continue
        data = _event_data(event)
        direct = data.get("tool_name")
        if isinstance(direct, str) and direct:
            names.append(direct)
        tools = data.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = tool.get("tool_name") or tool.get("name")
            if isinstance(tool_name, str) and tool_name:
                names.append(tool_name)
    return names


def _interrupt_reasons(events: list[dict[str, object]]) -> list[str]:
    reasons: list[str] = []
    for event in events:
        if event.get("event") not in {"interrupt_requested", "run_paused"}:
            continue
        data = _event_data(event)
        reason = data.get("reason")
        if isinstance(reason, str) and reason:
            reasons.append(reason)
        elif event.get("event") == "run_paused":
            reasons.append("run_paused")
    return reasons


def _assistant_text(events: list[dict[str, object]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event") != "token_delta":
            continue
        data = _event_data(event)
        chunk = data.get("delta_text") or data.get("text") or data.get("content")
        if isinstance(chunk, str):
            chunks.append(chunk)
    return "".join(chunks)


def _requires_research(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> bool:
    if (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    ):
        return True
    text = " ".join((user_prompt or "").lower().split())
    return any(
        marker in text
        for marker in (
            "найди",
            "поиск",
            "интернет",
            "источник",
            "research",
            "search",
            "source",
        )
    )


def _planning_summary(
    events: list[dict[str, object]],
    tool_names: list[str],
) -> dict[str, Any]:
    planning_tool_count = sum(1 for name in tool_names if name in PLANNING_TOOL_NAMES)
    data_tool_count = sum(1 for name in tool_names if name not in PLANNING_TOOL_NAMES)
    snapshots = 0
    latest_snapshot: dict[str, Any] | None = None
    for event in events:
        snapshot = _event_data(event).get("planning_snapshot")
        if isinstance(snapshot, dict):
            snapshots += 1
            latest_snapshot = dict(snapshot)
    if planning_tool_count == 0:
        verdict = None
    else:
        verdict = "engaged" if data_tool_count > 0 else "fabricated"
    return {
        "verdict": verdict,
        "planning_tool_calls": planning_tool_count,
        "data_tool_calls": data_tool_count,
        "snapshots": snapshots,
        "latest_snapshot": latest_snapshot,
    }


def _planning_execution_expected(
    *,
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
) -> bool:
    if requires_research:
        return True
    prompt = " ".join((user_prompt or "").lower().split())
    if any(marker in prompt for marker in ("выполни", "execute", "implement", "fix")):
        return True
    answer = assistant_text.lower()
    return any(
        marker in answer
        for marker in (
            "данные собраны",
            "источники изучены",
            "были выполнены",
            "проведён поиск",
            "проведен поиск",
            "research completed",
            "data collected",
        )
    )


def _notes(
    *,
    failures: dict[str, bool],
    continuation_reason: str | None,
    interrupt_reasons: list[str],
) -> list[str]:
    notes: list[str] = []
    if failures["missing_required_research_evidence"]:
        notes.append(
            "Research was required, but no web_search/web_fetch tool call is visible."
        )
    if failures["progress_only_final"]:
        notes.append(
            "Final assistant text looks like progress narration, not a deliverable."
        )
    if failures["text_form_tool_call"]:
        notes.append(
            "Assistant emitted a plain-text tool call instead of native tool-call JSON."
        )
    if failures["fabricated_planning"]:
        notes.append("Planning tools ran, but no data/execution tool followed.")
    if interrupt_reasons:
        notes.append("Run paused for interrupt: " + ", ".join(interrupt_reasons))
    if continuation_reason and not any(notes):
        notes.append(f"Continuation detector reason: {continuation_reason}.")
    return notes


__all__ = ["summarize_run_trace"]
