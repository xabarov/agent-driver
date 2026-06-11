"""Tool and event extraction helpers for run trace summaries."""

from __future__ import annotations

from typing import Any


def count_events(events: list[dict[str, object]], event_name: str) -> int:
    return sum(1 for event in events if event.get("event") == event_name)


def event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def event_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
    tools = data.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    direct = data.get("tool_name")
    if isinstance(direct, str) and direct:
        return [data]
    return []


def tool_names(events: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for tool in _preferred_tool_payloads(events):
        tool_name = tool.get("tool_name") or tool.get("name")
        if isinstance(tool_name, str) and tool_name:
            names.append(tool_name)
    return names


def tool_payloads(
    events: list[dict[str, object]],
    tool_name: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for tool in _preferred_tool_payloads(events):
        if tool.get("tool_name") == tool_name or tool.get("name") == tool_name:
            payloads.append(tool)
    return payloads


def _preferred_tool_payloads(events: list[dict[str, object]]) -> list[dict[str, Any]]:
    completed = _tool_payloads_for_event(events, "tool_call_completed")
    if completed:
        return completed
    return _tool_payloads_for_event(events, "tool_call_started")


def _tool_payloads_for_event(
    events: list[dict[str, object]],
    event_name: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != event_name:
            continue
        payloads.extend(event_tools(event_data(event)))
    return payloads


def interrupt_reasons(events: list[dict[str, object]]) -> list[str]:
    reasons: list[str] = []
    for event in events:
        if event.get("event") not in {"interrupt_requested", "run_paused"}:
            continue
        data = event_data(event)
        reason = data.get("reason")
        if isinstance(reason, str) and reason:
            reasons.append(reason)
        elif event.get("event") == "run_paused":
            reasons.append("run_paused")
    return reasons


def assistant_text(events: list[dict[str, object]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event") != "token_delta":
            continue
        data = event_data(event)
        chunk = data.get("delta_text") or data.get("text") or data.get("content")
        if isinstance(chunk, str):
            chunks.append(chunk)
    return "".join(chunks)


def unknown_tool_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    names: list[str] = []
    suggestions: list[str] = []
    for event in events:
        if event.get("event") != "tool_call_completed":
            continue
        data = event_data(event)
        for tool in event_tools(data):
            if str(tool.get("error_code") or "") != "tool_not_registered":
                continue
            name = tool.get("tool_name")
            if isinstance(name, str) and name:
                names.append(name)
            summary = tool.get("result_summary")
            if isinstance(summary, str) and summary:
                suggestions.append(summary)
    return {
        "count": len(names),
        "names": dedupe_preserve_order(names),
        "suggestions": suggestions[:3],
    }


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


__all__ = [
    "assistant_text",
    "count_events",
    "dedupe_preserve_order",
    "event_data",
    "event_tools",
    "interrupt_reasons",
    "tool_names",
    "tool_payloads",
    "unknown_tool_summary",
]
