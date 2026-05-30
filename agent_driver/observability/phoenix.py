"""Phoenix/OpenTelemetry runtime helpers.

This module owns the optional Phoenix lifecycle so host apps do not need to
copy small singleton setups. Host adapters can add domain-specific attribute
names, while the helpers here keep the common agent/runtime shape consistent.
"""

from __future__ import annotations

import json
from contextlib import nullcontext
from dataclasses import dataclass
from typing import Any

from agent_driver.contracts.runtime import AgentRunInput


@dataclass(frozen=True, slots=True)
class PhoenixTracingConfig:
    """Configuration for optional Phoenix tracing setup."""

    enabled: bool = False
    project_name: str = "agent-driver"
    collector_endpoint: str | None = None
    auto_instrument: bool = False
    batch: bool = False


_TRACING_READY = False
_TRACING_ERROR: str | None = None


def normalize_phoenix_http_endpoint(endpoint: str) -> str:
    """Return Phoenix OTLP HTTP trace endpoint for user-friendly base URLs."""
    normalized = endpoint.rstrip("/")
    if normalized.endswith("/v1/traces"):
        return normalized
    return f"{normalized}/v1/traces"


def setup_phoenix_tracing(config: PhoenixTracingConfig) -> dict[str, object]:
    """Initialize Phoenix tracing once and return current status."""
    global _TRACING_READY, _TRACING_ERROR  # pylint: disable=global-statement
    if _TRACING_READY or _TRACING_ERROR is not None:
        return phoenix_tracing_status()
    if not config.enabled:
        return phoenix_tracing_status()
    try:
        from phoenix.otel import register

        kwargs: dict[str, object] = {
            "project_name": config.project_name,
            "auto_instrument": config.auto_instrument,
            "batch": config.batch,
        }
        if config.collector_endpoint:
            kwargs["endpoint"] = normalize_phoenix_http_endpoint(
                config.collector_endpoint
            )
            kwargs["protocol"] = "http/protobuf"
        register(**kwargs)
        _TRACING_READY = True
    except Exception as exc:  # pragma: no cover - optional dependency fallback
        _TRACING_ERROR = str(exc)
    return phoenix_tracing_status()


def phoenix_tracing_status() -> dict[str, object]:
    """Return current Phoenix tracing setup state."""
    return {
        "enabled": _TRACING_READY,
        "error": _TRACING_ERROR,
    }


def get_otel_tracer(tracer_name: str):
    """Return an OpenTelemetry tracer or ``None`` when dependency is absent."""
    try:
        from opentelemetry import trace

        return trace.get_tracer(tracer_name)
    except Exception:  # pragma: no cover - optional dependency fallback
        return None


def start_otel_span(
    span_name: str,
    *,
    tracer_name: str,
    attributes: dict[str, object] | None = None,
):
    """Start an OTel span, or a no-op context manager when tracing is absent."""
    tracer = get_otel_tracer(tracer_name)
    if tracer is None:
        return nullcontext()
    return tracer.start_as_current_span(span_name, attributes=attributes or {})


def trace_otel_event_span(
    span_name: str,
    *,
    tracer_name: str,
    attributes: dict[str, object] | None = None,
    data_attribute: tuple[str, object] | None = None,
) -> None:
    """Record one child span with optional serialized payload data."""
    tracer = get_otel_tracer(tracer_name)
    if tracer is None:
        return
    with tracer.start_as_current_span(span_name, attributes=attributes or {}) as span:
        if data_attribute is not None:
            key, value = data_attribute
            span.set_attribute(key, safe_json(value))


def agent_run_otel_attributes(
    run_input: AgentRunInput,
    *,
    app_metadata_attributes: dict[str, str] | None = None,
) -> dict[str, object]:
    """Build stable OpenTelemetry attributes for an agent run."""
    app_metadata = (
        run_input.app_metadata if isinstance(run_input.app_metadata, dict) else {}
    )
    policy_metadata = (
        run_input.tool_policy.metadata
        if run_input.tool_policy is not None
        and isinstance(run_input.tool_policy.metadata, dict)
        else {}
    )
    task_contract = policy_metadata.get("task_contract")
    task_kind = ""
    task_requires_research = False
    if isinstance(task_contract, dict):
        task_kind = str(task_contract.get("kind") or "")
        task_requires_research = task_contract.get("requires_research") is True

    attrs: dict[str, object] = {
        "agent.run_id": run_input.run_id or "",
        "agent.thread_id": run_input.thread_id or "",
        "agent.agent_id": run_input.agent_id or "",
        "agent.graph_preset": run_input.graph_preset or "",
        "agent.input": str(run_input.input)[:1200],
        "planning.hint": safe_json(policy_metadata.get("planning_hint")),
        "force_planning": safe_json(policy_metadata.get("force_planning")),
        "task_contract.kind": task_kind,
        "task_contract.requires_research": task_requires_research,
        "tool_choice.effective": safe_json(run_input.tool_choice),
    }
    for metadata_key, attribute_name in (app_metadata_attributes or {}).items():
        attrs[attribute_name] = _otel_scalar(app_metadata.get(metadata_key))
    return attrs


def runtime_event_otel_attributes(
    event_name: str,
    data: object,
) -> dict[str, object] | None:
    """Build compact OpenTelemetry attributes for one runtime event.

    Token deltas are intentionally skipped: they are high-volume and already
    visible through the persisted event stream.
    """
    if event_name == "token_delta":
        return None
    attrs: dict[str, object] = {"runtime.event": event_name}
    if not isinstance(data, dict):
        return attrs
    reason = data.get("reason")
    if isinstance(reason, str):
        attrs["runtime.reason"] = reason
    finish_reason = data.get("finish_reason")
    if isinstance(finish_reason, str):
        attrs["runtime.finish_reason"] = finish_reason
    tools = data.get("tools")
    if isinstance(tools, list):
        attrs["tool.names"] = ",".join(_names_from_dict_items(tools, "tool_name"))
        attrs["tool.statuses"] = ",".join(_names_from_dict_items(tools, "status"))
    planned = data.get("planned_tool_calls")
    if isinstance(planned, list):
        attrs["llm.planned_tool_names"] = ",".join(
            _names_from_dict_items(planned, "tool_name")
        )
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
        current = planning_snapshot.get("in_progress_id")
        if isinstance(completed, int):
            attrs["planning.completed"] = completed
        if isinstance(total, int):
            attrs["planning.total"] = total
        if isinstance(current, str):
            attrs["planning.in_progress_id"] = current
    return attrs


def _otel_scalar(value: object) -> object:
    if value is None:
        return ""
    if isinstance(value, (str, bool, int, float)):
        return value
    return safe_json(value)


def _names_from_dict_items(items: list[object], key: str) -> list[str]:
    return [
        str(item.get(key)) for item in items if isinstance(item, dict) and item.get(key)
    ]


def safe_json(value: object, *, max_chars: int = 1200) -> str:
    """Serialize observability payloads without crashing on non-JSON values."""
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except TypeError:
        text = str(value)
    return text if len(text) <= max_chars else f"{text[:max_chars]}..."


def _reset_phoenix_tracing_for_tests() -> None:
    """Reset process singleton state for unit tests."""
    global _TRACING_READY, _TRACING_ERROR  # pylint: disable=global-statement
    _TRACING_READY = False
    _TRACING_ERROR = None


__all__ = [
    "PhoenixTracingConfig",
    "agent_run_otel_attributes",
    "get_otel_tracer",
    "normalize_phoenix_http_endpoint",
    "phoenix_tracing_status",
    "runtime_event_otel_attributes",
    "safe_json",
    "setup_phoenix_tracing",
    "start_otel_span",
    "trace_otel_event_span",
]
