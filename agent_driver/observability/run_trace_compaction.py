"""Compaction and context-pressure analyzers for run trace summaries."""

from __future__ import annotations

from typing import Any


def compaction_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    """Summarize memory compaction attempts and latest outcome."""
    compaction_events = [
        event
        for event in events
        if event.get("event") in {"memory_compaction_started", "memory_compacted"}
    ]
    started = [
        event
        for event in compaction_events
        if event.get("event") == "memory_compaction_started"
    ]
    outcomes = [
        event_data(event)
        for event in compaction_events
        if event.get("event") == "memory_compacted"
    ]
    outcome_counts = {
        "successful": sum(
            1 for data in outcomes if data.get("outcome") == "successful"
        ),
        "failed": sum(1 for data in outcomes if data.get("outcome") == "failed"),
        "skipped": sum(1 for data in outcomes if data.get("outcome") == "skipped"),
    }
    modes: list[str] = []
    for event in compaction_events:
        mode = event_data(event).get("mode")
        if isinstance(mode, str) and mode and mode not in modes:
            modes.append(mode)
    latest_data = event_data(compaction_events[-1]) if compaction_events else {}
    latest_state = latest_data.get("compaction_state")
    latest = None
    if compaction_events:
        latest = {
            "event": compaction_events[-1].get("event"),
            "outcome": latest_data.get("outcome"),
            "mode": latest_data.get("mode"),
            "compaction_id": latest_data.get("compaction_id"),
            "failure_kind": latest_data.get("failure_kind"),
            "summarized_message_count": latest_data.get("summarized_message_count"),
        }
    return {
        "attempts": max(
            len(started), outcome_counts["successful"] + outcome_counts["failed"]
        ),
        "started": len(started),
        **outcome_counts,
        "modes": modes,
        "circuit_breaker_open": (
            latest_state.get("circuit_breaker_open")
            if isinstance(latest_state, dict)
            else False
        ),
        "latest": latest,
    }


def context_pressure_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    compaction: dict[str, Any],
) -> dict[str, Any]:
    """Summarize pressure diagnostics and whether the run reacted."""
    diagnostics: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "warning":
            continue
        data = event_data(event)
        if data.get("kind") != "token_pressure":
            continue
        state = data.get("state")
        signal_id = data.get("signal_id")
        if not isinstance(state, str) or not isinstance(signal_id, str):
            continue
        diagnostics.append(
            {
                "state": state,
                "signal_id": signal_id,
                "severity": data.get("severity"),
                "recommendation": data.get("recommendation"),
                "context_usage_ratio": data.get("context_usage_ratio"),
            }
        )
    states = [item["state"] for item in diagnostics]
    recommendations = [
        str(item["recommendation"])
        for item in diagnostics
        if isinstance(item.get("recommendation"), str)
    ]
    latest = diagnostics[-1] if diagnostics else None
    delegated = "agent_tool" in tool_names
    compaction_attempted = int(compaction.get("attempts") or 0) > 0
    ignored = False
    if latest is not None:
        latest_recommendation = latest.get("recommendation")
        if latest_recommendation in {"compact_recommended", "blocking"}:
            ignored = not compaction_attempted
        elif latest_recommendation == "delegate_or_summarize":
            ignored = not delegated and not compaction_attempted
    return {
        "diagnostic_count": len(diagnostics),
        "states": states,
        "recommendations": recommendations,
        "latest": latest,
        "delegated_after_recommendation": delegated if diagnostics else False,
        "compaction_attempted_after_recommendation": (
            compaction_attempted if diagnostics else False
        ),
        "ignored_latest_recommendation": ignored,
    }


def event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


__all__ = ["compaction_summary", "context_pressure_summary"]
