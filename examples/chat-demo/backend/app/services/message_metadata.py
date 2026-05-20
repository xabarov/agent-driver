"""Aggregate assistant message metadata from projected stream events."""

from __future__ import annotations

from typing import Any


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


def merge_metadata(previous: dict[str, Any] | None, patch: dict[str, Any]) -> dict[str, Any]:
    """Merge LLM step patches for one assistant turn."""
    base = dict(previous or {})
    for key in ("promptTokens", "completionTokens", "totalTokens"):
        if key in patch:
            base[key] = int(base.get(key, 0) or 0) + int(patch.get(key, 0) or 0)
    if "durationMs" in patch:
        base["durationMs"] = float(base.get("durationMs", 0) or 0) + float(patch["durationMs"])
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


def aggregate_metadata_from_events(events: list[dict[str, object]]) -> dict[str, Any]:
    """Build OpenRouter-style metadata from a run event list."""
    metadata: dict[str, Any] | None = None
    for event in events:
        if str(event.get("event")) != "llm_call_completed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        metadata = merge_metadata(metadata, _parse_llm_completed(data))
    return metadata or {}
