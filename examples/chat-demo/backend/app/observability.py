"""Chat-demo observability adapter over agent_driver Phoenix helpers."""

from __future__ import annotations

from app.config import Settings

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.observability import (
    PhoenixTracingConfig,
    agent_run_otel_attributes,
    phoenix_tracing_status,
    runtime_event_otel_attributes,
    setup_phoenix_tracing,
    start_otel_span,
    trace_otel_event_span,
)

_TRACER_NAME = "agent-driver.chat-demo"


def setup_tracing(settings: Settings) -> None:
    """Initialize Phoenix tracing once when enabled."""
    setup_phoenix_tracing(
        PhoenixTracingConfig(
            enabled=settings.tracing_enabled,
            project_name=settings.phoenix_project_name,
            collector_endpoint=settings.phoenix_collector_endpoint,
            auto_instrument=False,
            batch=False,
        )
    )


def tracing_status() -> dict[str, object]:
    """Return current tracing setup state for health/debug endpoints."""
    return phoenix_tracing_status()


def run_attributes(run_input: AgentRunInput) -> dict[str, object]:
    """Build stable attributes for a chat-demo run span."""
    return agent_run_otel_attributes(
        run_input,
        app_metadata_attributes={
            "session_id": "chat.session_id",
            "chat_mode": "chat.mode",
        },
    )


def start_run_span(run_input: AgentRunInput):
    """Start a run span or return a no-op context manager."""
    return start_otel_span(
        "chat_demo.run",
        tracer_name=_TRACER_NAME,
        attributes=run_attributes(run_input),
    )


def trace_runtime_event(event_name: str, data: object) -> None:
    """Record one important runtime event under the active run span."""
    attrs = runtime_event_otel_attributes(event_name, data)
    if attrs is None:
        return
    trace_otel_event_span(
        f"runtime.{event_name}",
        tracer_name=_TRACER_NAME,
        attributes=attrs,
        data_attribute=("runtime.data", data),
    )


__all__ = [
    "setup_tracing",
    "start_run_span",
    "trace_runtime_event",
    "tracing_status",
]
