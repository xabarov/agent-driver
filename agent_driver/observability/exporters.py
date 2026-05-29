"""Local/no-op trace exporters for Phase-5 observability baseline."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.contracts import TraceExport, TraceSinkResult


class NoOpTraceExporter:  # pylint: disable=too-few-public-methods
    """Exporter that validates payload and returns sink metadata only."""

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Return deterministic sink result without persistence."""
        _ = payload
        return TraceSinkResult(
            sink="noop", trace_id=payload.trace_id, span_count=len(payload.spans)
        )


class LocalTraceExporter:
    """In-memory exporter for deterministic local tests/devtools."""

    def __init__(self) -> None:
        self._exports: dict[str, TraceExport] = {}

    def export(self, payload: TraceExport) -> TraceSinkResult:
        """Store trace payload by trace identifier and report sink metadata."""
        self._exports[payload.trace_id] = payload
        return TraceSinkResult(
            sink="local_memory",
            trace_id=payload.trace_id,
            span_count=len(payload.spans),
            metadata={"stored_traces": len(self._exports)},
        )

    def get(self, trace_id: str) -> TraceExport | None:
        """Return stored trace export by id."""
        return self._exports.get(trace_id)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        """Return JSON-safe snapshot summary for tests/debug."""
        result: dict[str, dict[str, Any]] = {}
        for trace_id, payload in self._exports.items():
            result[trace_id] = {
                "run_id": payload.run_id,
                "attempt_id": payload.attempt_id,
                "span_count": len(payload.spans),
            }
        return result
