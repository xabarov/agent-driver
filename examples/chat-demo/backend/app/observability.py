"""Phoenix/OpenTelemetry tracing helpers for chat-demo."""

from __future__ import annotations

import json
from typing import Any

from app.config import Settings

from agent_driver.contracts.runtime import AgentRunInput

_TRACING_READY = False
_TRACING_ERROR: str | None = None


def setup_tracing(settings: Settings) -> None:
    """Initialize Phoenix tracing once when enabled."""
    global _TRACING_READY, _TRACING_ERROR  # noqa: PLW0603 - process singleton setup
    if _TRACING_READY or _TRACING_ERROR is not None:
        return
    if not settings.tracing_enabled:
        return
    try:
        from phoenix.otel import register

        kwargs: dict[str, object] = {
            "project_name": settings.phoenix_project_name,
            "auto_instrument": False,
            "batch": False,
        }
        if settings.phoenix_collector_endpoint:
            kwargs["endpoint"] = _normalize_http_endpoint(
                settings.phoenix_collector_endpoint
            )
            kwargs["protocol"] = "http/protobuf"
        register(**kwargs)
        _TRACING_READY = True
    except Exception as exc:  # pragma: no cover - optional integration fallback
        _TRACING_ERROR = str(exc)


def tracing_status() -> dict[str, object]:
    """Return current tracing setup state for health/debug endpoints."""
    return {
        "enabled": _TRACING_READY,
        "error": _TRACING_ERROR,
    }


def _normalize_http_endpoint(endpoint: str) -> str:
    """Return Phoenix OTLP HTTP trace endpoint for user-friendly base URLs."""
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v1/traces"):
        return normalized
    return f"{normalized}/v1/traces"


def _get_tracer():
    try:
        from opentelemetry import trace

        return trace.get_tracer("agent-driver.chat-demo")
    except Exception:  # pragma: no cover - optional dependency fallback
        return None


def _safe_json(value: object, *, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return text if len(text) <= max_chars else f"{text[:max_chars]}..."


def run_attributes(run_input: AgentRunInput) -> dict[str, object]:
    """Build stable attributes for a chat-demo run span."""
    app_metadata = (
        run_input.app_metadata if isinstance(run_input.app_metadata, dict) else {}
    )
    policy_metadata = (
        run_input.tool_policy.metadata
        if run_input.tool_policy is not None
        and isinstance(run_input.tool_policy.metadata, dict)
        else {}
    )
    return {
        "agent.run_id": run_input.run_id or "",
        "agent.thread_id": run_input.thread_id or "",
        "agent.agent_id": run_input.agent_id or "",
        "agent.graph_preset": run_input.graph_preset or "",
        "agent.input": str(run_input.input)[:1200],
        "chat.session_id": str(app_metadata.get("session_id") or ""),
        "chat.mode": bool(app_metadata.get("chat_mode")),
        "planning.hint": _safe_json(policy_metadata.get("planning_hint")),
        "force_planning": _safe_json(policy_metadata.get("force_planning")),
    }


def start_run_span(run_input: AgentRunInput):
    """Start a run span or return a no-op context manager."""
    tracer = _get_tracer()
    if tracer is None:
        from contextlib import nullcontext

        return nullcontext()
    return tracer.start_as_current_span(
        "chat_demo.run",
        attributes=run_attributes(run_input),
    )


def trace_runtime_event(event_name: str, data: object) -> None:
    """Record one important runtime event under the active run span."""
    if event_name == "token_delta":
        return
    tracer = _get_tracer()
    if tracer is None:
        return
    attrs: dict[str, object] = {"runtime.event": event_name}
    if isinstance(data, dict):
        reason = data.get("reason")
        if isinstance(reason, str):
            attrs["runtime.reason"] = reason
        finish_reason = data.get("finish_reason")
        if isinstance(finish_reason, str):
            attrs["runtime.finish_reason"] = finish_reason
        tools = data.get("tools")
        if isinstance(tools, list):
            names = [
                str(item.get("tool_name"))
                for item in tools
                if isinstance(item, dict) and item.get("tool_name")
            ]
            statuses = [
                str(item.get("status"))
                for item in tools
                if isinstance(item, dict) and item.get("status")
            ]
            attrs["tool.names"] = ",".join(names)
            attrs["tool.statuses"] = ",".join(statuses)
        planned = data.get("planned_tool_calls")
        if isinstance(planned, list):
            names = [
                str(item.get("tool_name"))
                for item in planned
                if isinstance(item, dict) and item.get("tool_name")
            ]
            attrs["llm.planned_tool_names"] = ",".join(names)
        usage = data.get("usage")
        if isinstance(usage, dict):
            for key in ("input_tokens", "output_tokens", "total_tokens"):
                value = usage.get(key)
                if isinstance(value, int):
                    attrs[f"llm.usage.{key}"] = value
        planning_snapshot = data.get("planning_snapshot")
        if isinstance(planning_snapshot, dict):
            completed = planning_snapshot.get("completed")
            total = planning_snapshot.get("total")
            if isinstance(completed, int):
                attrs["planning.completed"] = completed
            if isinstance(total, int):
                attrs["planning.total"] = total
            current = planning_snapshot.get("in_progress_id")
            if isinstance(current, str):
                attrs["planning.in_progress_id"] = current
    with tracer.start_as_current_span(
        f"runtime.{event_name}", attributes=attrs
    ) as span:
        span.set_attribute("runtime.data", _safe_json(data))


__all__ = [
    "setup_tracing",
    "start_run_span",
    "trace_runtime_event",
    "tracing_status",
]
