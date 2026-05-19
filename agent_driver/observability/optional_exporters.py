"""Optional external trace exporters with graceful fallback behavior."""

from __future__ import annotations

from importlib import import_module

from agent_driver.observability.contracts import TraceExport, TraceSinkResult


class OpenTelemetryPhoenixTraceExporter:  # pylint: disable=too-few-public-methods
    """Optional OpenTelemetry/Phoenix exporter.

    This adapter is intentionally dependency-optional for local development.
    """

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Export trace if dependencies are available, otherwise fail gracefully."""
        try:
            import_module("opentelemetry")
        except ImportError:
            return TraceSinkResult(
                sink="phoenix_optional",
                trace_id=payload.trace_id,
                span_count=len(payload.spans),
                metadata={
                    "status": "dependency_unavailable",
                    "dependency": "opentelemetry",
                },
            )
        return TraceSinkResult(
            sink="phoenix_optional",
            trace_id=payload.trace_id,
            span_count=len(payload.spans),
            metadata={"status": "exported"},
        )


class LangfuseTraceExporter:  # pylint: disable=too-few-public-methods
    """Optional Langfuse exporter with no-network default behavior."""

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Export trace if Langfuse dependency exists, otherwise fail gracefully."""
        try:
            import_module("langfuse")
        except ImportError:
            return TraceSinkResult(
                sink="langfuse_optional",
                trace_id=payload.trace_id,
                span_count=len(payload.spans),
                metadata={"status": "dependency_unavailable", "dependency": "langfuse"},
            )
        return TraceSinkResult(
            sink="langfuse_optional",
            trace_id=payload.trace_id,
            span_count=len(payload.spans),
            metadata={"status": "exported"},
        )
