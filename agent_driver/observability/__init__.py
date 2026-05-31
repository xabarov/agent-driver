"""Trace export and telemetry sinks (Phase 5: observability)."""

from agent_driver.observability.contracts import (
    TraceExport,
    TraceExporter,
    TraceSinkResult,
    TraceSpan,
)
from agent_driver.observability.exporters import LocalTraceExporter, NoOpTraceExporter
from agent_driver.observability.message_metadata import (
    aggregate_message_metadata_from_events,
    merge_message_metadata,
)
from agent_driver.observability.optional_exporters import (
    LangfuseTraceExporter,
    OpenTelemetryPhoenixTraceExporter,
    SpanAttributeResolver,
)
from agent_driver.observability.phoenix import (
    PhoenixTracingConfig,
    agent_run_otel_attributes,
    get_otel_tracer,
    normalize_phoenix_http_endpoint,
    phoenix_tracing_status,
    runtime_event_otel_attributes,
    safe_json,
    setup_phoenix_tracing,
    start_otel_span,
    trace_otel_event_span,
)
from agent_driver.observability.run_trace.summary import summarize_run_trace
from agent_driver.observability.support_bundle import (
    build_persisted_support_bundle,
    build_runtime_support_bundle,
)
from agent_driver.observability.trace_builder import build_trace_export

__all__ = [
    "LocalTraceExporter",
    "NoOpTraceExporter",
    "aggregate_message_metadata_from_events",
    "OpenTelemetryPhoenixTraceExporter",
    "PhoenixTracingConfig",
    "LangfuseTraceExporter",
    "SpanAttributeResolver",
    "agent_run_otel_attributes",
    "build_persisted_support_bundle",
    "build_runtime_support_bundle",
    "get_otel_tracer",
    "normalize_phoenix_http_endpoint",
    "phoenix_tracing_status",
    "runtime_event_otel_attributes",
    "safe_json",
    "merge_message_metadata",
    "setup_phoenix_tracing",
    "start_otel_span",
    "summarize_run_trace",
    "TraceExport",
    "TraceExporter",
    "TraceSinkResult",
    "TraceSpan",
    "trace_otel_event_span",
    "build_trace_export",
]
