"""Deterministic runtime-to-trace projection helpers."""

from __future__ import annotations

from hashlib import sha1

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.observability.contracts import TraceExport, TraceSpan


def _trace_id_for_output(output: AgentRunOutput) -> str:
    """Build stable trace identifier for one run/attempt pair."""
    seed = f"{output.run_id}:{output.attempt_id}"
    return f"trace_{sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def _span_id_for_event(trace_id: str, event_id: str) -> str:
    """Build deterministic span id from trace and event identifiers."""
    seed = f"{trace_id}:{event_id}"
    return f"span_{sha1(seed.encode('utf-8')).hexdigest()[:16]}"


def build_trace_export(output: AgentRunOutput) -> TraceExport:
    """Project one run output into deterministic trace export payload."""
    trace_id = output.trace.trace_id if output.trace and output.trace.trace_id else None
    resolved_trace_id = trace_id or _trace_id_for_output(output)
    spans = [
        TraceSpan(
            span_id=_span_id_for_event(resolved_trace_id, event.event_id),
            event_id=event.event_id,
            run_id=event.run_id,
            attempt_id=event.attempt_id,
            seq=event.seq,
            event_type=event.type.value,
            node_id=event.node_id,
            checkpoint_id=event.checkpoint_id,
            created_at=event.created_at,
            payload=event.payload,
            metadata={"severity": event.severity.value},
        )
        for event in sorted(output.events, key=lambda item: item.seq)
    ]
    return TraceExport(
        trace_id=resolved_trace_id,
        run_id=output.run_id,
        attempt_id=output.attempt_id,
        spans=spans,
        tool_trace=[item.model_dump(mode="json") for item in output.tool_trace],
        usage=output.usage.model_dump(mode="json") if output.usage else None,
        checkpoint=(
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        metadata={
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
            "warning_count": len(output.warnings),
        },
    )
