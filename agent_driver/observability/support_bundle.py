"""Redaction-safe support-bundle primitives for runtime/eval workflows."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.observability.trace_builder import build_trace_export

_SECRET_KEY_MARKERS = ("token", "secret", "password", "api_key", "auth")


def _is_sensitive_key(key: str) -> bool:
    lower = key.lower()
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


def build_runtime_support_bundle(output: AgentRunOutput) -> dict[str, Any]:
    """Build support bundle from runtime output with deterministic redaction."""
    trace_export = build_trace_export(output)
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
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }


__all__ = ["build_persisted_support_bundle", "build_runtime_support_bundle"]
