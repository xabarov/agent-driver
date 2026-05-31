"""Provider and LLM-call analyzers for run trace summaries."""

from __future__ import annotations

from typing import Any


def provider_rejected(events: list[dict[str, object]]) -> bool:
    return any(event.get("event") == "llm_request_rejected" for event in events)


def provider_profile_summary(
    events: list[dict[str, object]],
) -> dict[str, Any] | None:
    """Return latest provider capability profile recorded by the LLM layer."""
    for event in reversed(events):
        if event.get("event") != "llm_call_completed":
            continue
        data = event_data(event)
        profile = data.get("provider_profile")
        if isinstance(profile, dict):
            return profile
    return None


def llm_call_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    tool_choices: list[Any] = []
    force_final_reasons: list[str] = []
    continuation_reasons: list[str] = []
    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
        "cost_usd_estimate": 0.0,
    }
    saw_usage = False
    for event in events:
        if event.get("event") == "llm_call_completed":
            data = event_data(event)
            event_usage = data.get("usage")
            if isinstance(event_usage, dict):
                saw_usage = True
                usage["input_tokens"] += _usage_int(
                    event_usage.get("input_tokens", event_usage.get("prompt_tokens"))
                )
                usage["output_tokens"] += _usage_int(
                    event_usage.get(
                        "output_tokens", event_usage.get("completion_tokens")
                    )
                )
                usage["total_tokens"] += _usage_int(event_usage.get("total_tokens"))
                usage["cost_usd_estimate"] += _usage_float(
                    event_usage.get("cost_usd_estimate")
                )
            continue
        if event.get("event") != "llm_call_started":
            continue
        data = event_data(event)
        if "tool_choice_effective" in data:
            tool_choices.append(data.get("tool_choice_effective"))
        force_final_reason = data.get("force_final_reason")
        if isinstance(force_final_reason, str) and force_final_reason:
            force_final_reasons.append(force_final_reason)
        continuation_reason = data.get("continuation_reason")
        if isinstance(continuation_reason, str) and continuation_reason:
            continuation_reasons.append(continuation_reason)
    return {
        "started": count_events(events, "llm_call_started"),
        "completed": count_events(events, "llm_call_completed"),
        "tool_choice_effective": tool_choices,
        "force_final_reasons": force_final_reasons,
        "continuation_reasons": continuation_reasons,
        "usage": usage if saw_usage else None,
    }


def prompt_surface_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    effective_tool_names: list[str] = []
    prompt_fragments: list[str] = []
    for event in events:
        if event.get("event") != "llm_call_completed":
            continue
        data = event_data(event)
        tools = data.get("effective_tool_names")
        if isinstance(tools, list):
            effective_tool_names.extend(
                item for item in tools if isinstance(item, str) and item
            )
        fragments = data.get("prompt_fragments")
        if isinstance(fragments, list):
            prompt_fragments.extend(
                item for item in fragments if isinstance(item, str) and item
            )
    return {
        "effective_tool_names": dedupe_preserve_order(effective_tool_names),
        "prompt_fragments": dedupe_preserve_order(prompt_fragments),
    }


def count_events(events: list[dict[str, object]], event_name: str) -> int:
    return sum(1 for event in events if event.get("event") == event_name)


def event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def _usage_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _usage_float(value: object) -> float:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0.0, float(value))
    return 0.0


__all__ = [
    "llm_call_summary",
    "prompt_surface_summary",
    "provider_profile_summary",
    "provider_rejected",
]
