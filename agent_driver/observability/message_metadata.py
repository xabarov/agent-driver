"""Aggregate UI-friendly message metadata from projected stream events."""

from __future__ import annotations

import re
from typing import Any

from agent_driver.observability.source_evidence import merge_source_evidence
from agent_driver.runtime.planning_check import PLANNING_TOOL_NAMES


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    return None


def _parse_usage_dict(usage: dict[str, Any]) -> dict[str, Any]:
    prompt = _as_int(usage.get("input_tokens", usage.get("prompt_tokens")))
    completion = _as_int(usage.get("output_tokens", usage.get("completion_tokens")))
    total = _as_int(usage.get("total_tokens"))
    if total is None and (prompt is not None or completion is not None):
        total = (prompt or 0) + (completion or 0)
    cost = _as_float(usage.get("cost_usd_estimate"))
    if cost is None:
        for key in ("total_cost", "cost", "generation_cost"):
            cost = _as_float(usage.get(key))
            if cost is not None:
                break
    patch: dict[str, Any] = {"estimated": True}
    if prompt is not None:
        patch["promptTokens"] = prompt
    if completion is not None:
        patch["completionTokens"] = completion
    if total is not None:
        patch["totalTokens"] = total
    if cost is not None:
        patch["costUsd"] = cost
    model = usage.get("model_name")
    if isinstance(model, str) and model:
        patch["model"] = model
    provider = usage.get("model_provider")
    if isinstance(provider, str) and provider:
        patch["provider"] = provider
    return patch


def _parse_llm_completed(data: dict[str, Any]) -> dict[str, Any]:
    patch: dict[str, Any] = {}
    usage = data.get("usage")
    if isinstance(usage, dict):
        patch.update(_parse_usage_dict(usage))
    duration_ms = _as_float(data.get("duration_ms"))
    if duration_ms is not None:
        patch["durationMs"] = patch.get("durationMs", 0) + duration_ms
    model = data.get("model")
    if isinstance(model, str) and model:
        patch["model"] = model
    provider = data.get("provider")
    if isinstance(provider, str) and provider:
        patch["provider"] = provider
    return patch


def merge_message_metadata(
    previous: dict[str, Any] | None,
    patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge LLM step patches for one assistant turn."""
    base = dict(previous or {})
    for key in ("promptTokens", "completionTokens", "totalTokens"):
        if key in patch:
            base[key] = int(base.get(key, 0) or 0) + int(patch.get(key, 0) or 0)
    if "durationMs" in patch:
        base["durationMs"] = float(base.get("durationMs", 0) or 0) + float(
            patch["durationMs"]
        )
    if "costUsd" in patch:
        base["costUsd"] = float(base.get("costUsd", 0) or 0) + float(patch["costUsd"])
    for key in ("model", "provider"):
        if key in patch:
            base[key] = patch[key]
    base["estimated"] = bool(patch.get("estimated", base.get("estimated", True)))
    completion = int(base.get("completionTokens", 0) or 0)
    duration_ms = float(base.get("durationMs", 0) or 0)
    if completion > 0 and duration_ms > 0:
        base["tokensPerSecond"] = completion / (duration_ms / 1000.0)
    elif "tokensPerSecond" in base:
        base.pop("tokensPerSecond", None)
    if "totalTokens" not in base and (
        base.get("promptTokens") is not None or base.get("completionTokens") is not None
    ):
        base["totalTokens"] = int(base.get("promptTokens", 0) or 0) + int(
            base.get("completionTokens", 0) or 0
        )
    return base


def _planning_verdict(events: list[dict[str, object]]) -> str | None:
    """Compute a planning-mode execution verdict from tool_call_completed events."""
    saw_planning = False
    saw_data = False
    for event in events:
        if str(event.get("event")) != "tool_call_completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        tools = data.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            name = tool.get("tool_name")
            if not isinstance(name, str) or not name:
                continue
            if name in PLANNING_TOOL_NAMES:
                saw_planning = True
            else:
                saw_data = True
            if saw_planning and saw_data:
                return "engaged"
    if not saw_planning:
        return None
    return "engaged" if saw_data else "fabricated"


def _compaction_metadata(events: list[dict[str, object]]) -> dict[str, Any] | None:
    started: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    for event in events:
        event_name = str(event.get("event"))
        if event_name not in {"memory_compaction_started", "memory_compacted"}:
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        if event_name == "memory_compaction_started":
            started.append(dict(data))
        else:
            completed.append(dict(data))

    visible_completed = [
        item for item in completed if item.get("outcome") in {"success", "failure"}
    ]
    if not started and not visible_completed:
        return None

    latest = (visible_completed or started)[-1]
    status = "running"
    if latest in visible_completed:
        status = "failed" if latest.get("outcome") == "failure" else "done"

    metadata: dict[str, Any] = {
        "status": status,
        "attempts": max(len(started), len(visible_completed)),
    }
    for key in (
        "compaction_id",
        "mode",
        "reason",
        "failure_kind",
        "summarized_message_count",
    ):
        value = latest.get(key)
        if value is not None:
            metadata[key] = value
    return metadata


def _source_evidence_metadata(events: list[dict[str, object]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for event in events:
        if str(event.get("event")) != "tool_call_completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        tools = data.get("tools")
        if isinstance(tools, list):
            for tool in tools:
                if not isinstance(tool, dict):
                    continue
                if _tool_source_evidence_failed(tool):
                    continue
                sources = tool.get("sources")
                if isinstance(sources, list):
                    records.extend(
                        dict(item) for item in sources if isinstance(item, dict)
                    )
            continue
        sources = data.get("sources")
        if isinstance(sources, list) and not _tool_source_evidence_failed(data):
            records.extend(dict(item) for item in sources if isinstance(item, dict))
    return merge_source_evidence(records)


def _tool_source_evidence_failed(tool: dict[str, Any]) -> bool:
    status = str(tool.get("status") or "").lower()
    if status in {"failed", "error", "denied", "timed_out", "timeout"}:
        return True
    status_code = tool.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code >= 400
    if tool.get("error_code") or tool.get("error"):
        return True
    summary = tool.get("result_summary")
    if isinstance(summary, str) and re.search(r"\bHTTP\s+[45]\d\d\b", summary):
        return True
    return False


def aggregate_message_metadata_from_events(
    events: list[dict[str, object]],
) -> dict[str, Any]:
    """Build compact assistant message metadata from projected stream events."""
    metadata: dict[str, Any] | None = None
    for event in events:
        if str(event.get("event")) != "llm_call_completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        metadata = merge_message_metadata(metadata, _parse_llm_completed(data))
    for event in events:
        if str(event.get("event")) != "run_completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        artifacts = data.get("deep_research_artifacts")
        if isinstance(artifacts, dict):
            metadata = metadata or {}
            metadata["deep_research_artifacts"] = dict(artifacts)
    verdict = _planning_verdict(events)
    if verdict is not None:
        metadata = metadata or {}
        metadata["planningExecuted"] = verdict
    compaction = _compaction_metadata(events)
    if compaction is not None:
        metadata = metadata or {}
        metadata["compaction"] = compaction
    source_evidence = _source_evidence_metadata(events)
    if source_evidence:
        metadata = metadata or {}
        metadata["source_evidence"] = source_evidence
    return metadata or {}


__all__ = [
    "aggregate_message_metadata_from_events",
    "merge_message_metadata",
]
