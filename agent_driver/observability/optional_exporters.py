"""Optional external trace exporters with graceful fallback behavior.

Both ``OpenTelemetryPhoenixTraceExporter`` and ``LangfuseTraceExporter`` accept
an optional ``span_attribute_resolver`` callable so host applications can
attach domain-specific attributes (tenant ids, scan profile ids, budget
status, etc.) to each exported span without subclassing the exporter or
forking the contract.

The resolver shape is intentionally minimal:

    def resolver(span: TraceSpan, payload: TraceExport) -> dict[str, str | int | float | bool]:
        ...

Constraints:

- keys that are not strings are silently dropped;
- values that are not JSON primitives (``str | int | float | bool``) are
  silently dropped — this lets the runtime stay safe even when the host
  resolver returns richer objects accidentally;
- a resolver that raises is caught; the exporter still finishes the
  export, and the failure is reported via the result ``metadata`` (the
  resolved attributes for that span are treated as empty).
"""

from __future__ import annotations

from collections.abc import Callable
from importlib import import_module
from typing import TYPE_CHECKING

from agent_driver.observability.contracts import (
    TraceExport,
    TraceSinkResult,
    TraceSpan,
)

if TYPE_CHECKING:
    SpanAttributes = dict[str, "str | int | float | bool"]
    SpanAttributeResolver = Callable[[TraceSpan, TraceExport], SpanAttributes]
else:
    SpanAttributes = dict
    SpanAttributeResolver = Callable


def _safe_resolve_attributes(
    resolver: "SpanAttributeResolver | None",
    span: TraceSpan,
    payload: TraceExport,
) -> tuple[dict[str, "str | int | float | bool"], str | None]:
    """Run a host-provided resolver safely; return (attributes, error_tag)."""
    if resolver is None:
        return {}, None
    try:
        result = resolver(span, payload)
    except Exception as exc:  # pylint: disable=broad-exception-caught
        return {}, f"resolver_error:{type(exc).__name__}"
    if not isinstance(result, dict):
        return {}, "resolver_invalid_type"
    safe: dict[str, str | int | float | bool] = {}
    for key, value in result.items():
        if not isinstance(key, str):
            continue
        # ``bool`` is a subclass of ``int``; keep it accepted, but the test
        # below uses an explicit tuple so the order does not matter.
        if isinstance(value, (str, int, float, bool)):
            safe[key] = value
    return safe, None


def _aggregate_resolved_attributes(
    resolver: "SpanAttributeResolver | None",
    payload: TraceExport,
) -> tuple[int, int, list[str]]:
    """Apply resolver across all spans; return (attr_count, span_count, errors)."""
    if resolver is None:
        return 0, 0, []
    attribute_count = 0
    spans_with_attributes = 0
    seen_errors: list[str] = []
    for span in payload.spans:
        attributes, error = _safe_resolve_attributes(resolver, span, payload)
        if error is not None:
            if error not in seen_errors:
                seen_errors.append(error)
            continue
        if attributes:
            spans_with_attributes += 1
            attribute_count += len(attributes)
    return attribute_count, spans_with_attributes, seen_errors


class OpenTelemetryPhoenixTraceExporter:  # pylint: disable=too-few-public-methods
    """Optional OpenTelemetry/Phoenix exporter.

    This adapter is intentionally dependency-optional for local development.
    Host applications may pass ``span_attribute_resolver`` to attach extra
    domain attributes (e.g. ``zion.tenant_id``) to each span when the
    OpenTelemetry SDK is wired into a real OTLP exporter.
    """

    def __init__(
        self,
        *,
        span_attribute_resolver: "SpanAttributeResolver | None" = None,
    ) -> None:
        self._span_attribute_resolver = span_attribute_resolver

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Export trace if dependencies are available, otherwise fail gracefully."""
        try:
            import_module("opentelemetry")
            dependency_available = True
        except ImportError:
            dependency_available = False
        attribute_count, spans_with_attributes, errors = _aggregate_resolved_attributes(
            self._span_attribute_resolver, payload
        )
        metadata: dict[str, str | int | float | bool | list[str]] = {}
        if dependency_available:
            metadata["status"] = "exported"
        else:
            metadata["status"] = "dependency_unavailable"
            metadata["dependency"] = "opentelemetry"
        if self._span_attribute_resolver is not None:
            metadata["custom_attribute_count"] = attribute_count
            metadata["custom_attribute_spans"] = spans_with_attributes
            if errors:
                metadata["custom_attribute_resolver_errors"] = errors
        return TraceSinkResult(
            sink="phoenix_optional",
            trace_id=payload.trace_id,
            span_count=len(payload.spans),
            metadata=metadata,
        )


class LangfuseTraceExporter:  # pylint: disable=too-few-public-methods
    """Optional Langfuse exporter with no-network default behavior.

    Accepts the same ``span_attribute_resolver`` shape as the Phoenix
    exporter so a host application can share one resolver across both sinks.
    """

    def __init__(
        self,
        *,
        span_attribute_resolver: "SpanAttributeResolver | None" = None,
    ) -> None:
        self._span_attribute_resolver = span_attribute_resolver

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Export trace if Langfuse dependency exists, otherwise fail gracefully."""
        try:
            import_module("langfuse")
            dependency_available = True
        except ImportError:
            dependency_available = False
        attribute_count, spans_with_attributes, errors = _aggregate_resolved_attributes(
            self._span_attribute_resolver, payload
        )
        metadata: dict[str, str | int | float | bool | list[str]] = {}
        if dependency_available:
            metadata["status"] = "exported"
        else:
            metadata["status"] = "dependency_unavailable"
            metadata["dependency"] = "langfuse"
        if self._span_attribute_resolver is not None:
            metadata["custom_attribute_count"] = attribute_count
            metadata["custom_attribute_spans"] = spans_with_attributes
            if errors:
                metadata["custom_attribute_resolver_errors"] = errors
        return TraceSinkResult(
            sink="langfuse_optional",
            trace_id=payload.trace_id,
            span_count=len(payload.spans),
            metadata=metadata,
        )


__all__ = [
    "LangfuseTraceExporter",
    "OpenTelemetryPhoenixTraceExporter",
    "SpanAttributeResolver",
]
