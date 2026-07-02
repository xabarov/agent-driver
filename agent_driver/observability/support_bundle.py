"""Redaction-safe support-bundle primitives for runtime/eval workflows."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.observability.trace_builder import build_trace_export
from agent_driver.runtime.stream import (
    project_runtime_event_timeline,
    project_runtime_events,
    summarize_runtime_session_diagnostics,
)

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
    if lower == "base_url" or lower.endswith("_base_url"):
        return True
    return any(marker in lower for marker in _SECRET_KEY_MARKERS)


def _redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if _is_sensitive_key(str(key)) else _redact_value(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact_value(item) for item in value]
    return value


def _latest_llm_payload(output: AgentRunOutput, key: str) -> dict[str, Any] | None:
    for event in sorted(output.events, key=lambda item: item.seq, reverse=True):
        if event.type.value != "llm_call_completed":
            continue
        value = event.payload.get(key)
        if isinstance(value, dict):
            return _redact_value(value)
    return None


def build_runtime_support_bundle(output: AgentRunOutput) -> dict[str, Any]:
    """Build support bundle from runtime output with deterministic redaction."""
    trace_export = build_trace_export(output)
    stream_events = project_runtime_events(output.events)
    diagnostics = summarize_runtime_session_diagnostics(
        stream_events,
        durability="runtime_output",
        harness_id=str(output.metadata.get("harness_id"))
        if output.metadata.get("harness_id")
        else None,
        adapter_id=str(output.metadata.get("adapter_id"))
        if output.metadata.get("adapter_id")
        else None,
        session_id=output.thread_id,
    )
    return {
        "run": {
            "run_id": output.run_id,
            "attempt_id": output.attempt_id,
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
        },
        "trace": trace_export.model_dump(mode="json"),
        "warnings": [item.model_dump(mode="json") for item in output.warnings],
        "tool_trace": [item.model_dump(mode="json") for item in output.tool_trace],
        "subagent_groups": [item.model_dump(mode="json") for item in output.subagent_groups],
        "subagent_runs": [item.model_dump(mode="json") for item in output.subagent_runs],
        "checkpoint": (
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        "runtime_timeline": {
            "diagnostics": diagnostics.model_dump(mode="json"),
            "rows": [
                row.model_dump(mode="json")
                for row in project_runtime_event_timeline(output.events)
            ],
        },
        "route_profile": _latest_llm_payload(output, "route_profile"),
        "provider_preflight": _latest_llm_payload(output, "provider_preflight"),
        "metadata": _redact_value(output.metadata),
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }


def build_persisted_support_bundle(persisted_replay: dict[str, Any]) -> dict[str, Any]:
    """Build support bundle from replay payload loaded from persistent stores."""
    events = persisted_replay.get("events")
    return {
        "run": {
            "run_id": persisted_replay.get("run_id"),
            "event_count": int(persisted_replay.get("event_count", 0)),
            "trajectory": persisted_replay.get("trajectory", []),
        },
        "latest_checkpoint": persisted_replay.get("latest_checkpoint"),
        "checkpoints": persisted_replay.get("checkpoints", []),
        "events": _redact_value(events if isinstance(events, list) else []),
        "metadata": _redact_value(persisted_replay.get("metadata", {})),
        "runtime_timeline": {
            "diagnostics": {
                "run_id": persisted_replay.get("run_id"),
                "durability": "persisted_replay",
                "timeline_row_count": 0,
                "last_seq": _last_persisted_seq(events if isinstance(events, list) else []),
                "reconnect_cursor": _persisted_reconnect_cursor(
                    persisted_replay.get("run_id"),
                    events if isinstance(events, list) else [],
                ),
                "redaction": {"safe_by_default": True},
            },
        },
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }


def _last_persisted_seq(events: list[Any]) -> int | None:
    seqs = [event.get("seq") for event in events if isinstance(event, dict)]
    ints = [seq for seq in seqs if isinstance(seq, int)]
    return max(ints) if ints else None


def _persisted_reconnect_cursor(run_id: object, events: list[Any]) -> str | None:
    last_seq = _last_persisted_seq(events)
    if not isinstance(run_id, str) or last_seq is None:
        return None
    return f"{run_id}:{last_seq}"


__all__ = ["build_persisted_support_bundle", "build_runtime_support_bundle"]
