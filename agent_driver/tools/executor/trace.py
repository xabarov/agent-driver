"""Build structured tool traces from compact specs."""

from __future__ import annotations

from agent_driver.contracts.enums import ToolTraceStatus
from agent_driver.contracts.tools import ToolCall, ToolManifest, ToolTrace
from agent_driver.tools.executor.specs import BlockSpec, TraceSpec


def build_tool_trace(spec: TraceSpec) -> ToolTrace:
    """Build ToolTrace from compact trace specification."""
    return ToolTrace(
        step=spec.index,
        tool_name=spec.call.tool_name,
        tool_call_id=spec.call.tool_call_id,
        status=spec.status,
        risk=spec.manifest.risk,
        side_effect=spec.manifest.side_effect,
        approval_mode=spec.manifest.approval_mode,
        result_summary=spec.summary,
        truncated=spec.truncated,
        error_code=spec.error_code,
    )


def trace_spec_denied(
    *,
    index: int,
    call: ToolCall,
    manifest: ToolManifest,
    summary: str | None = None,
    error_code: str | None = None,
) -> TraceSpec:
    """Spec for a denied tool trace row (policy / guardrail / approval gate)."""
    return TraceSpec(
        index=index,
        call=call,
        manifest=manifest,
        status=ToolTraceStatus.DENIED,
        summary=summary,
        error_code=error_code,
    )


def trace_spec_completed(
    *,
    index: int,
    call: ToolCall,
    manifest: ToolManifest,
    summary: str | None,
    truncated: bool,
) -> TraceSpec:
    """Spec for a successfully completed tool trace row."""
    return TraceSpec(
        index=index,
        call=call,
        manifest=manifest,
        status=ToolTraceStatus.COMPLETED,
        summary=summary,
        truncated=truncated,
    )


def trace_spec_failed(
    *,
    index: int,
    call: ToolCall,
    manifest: ToolManifest,
    summary: str | None,
    error_code: str | None,
    truncated: bool = False,
) -> TraceSpec:
    """Spec for a tool that executed but self-reported failure (success_field)."""
    return TraceSpec(
        index=index,
        call=call,
        manifest=manifest,
        status=ToolTraceStatus.FAILED,
        summary=summary,
        error_code=error_code,
        truncated=truncated,
    )


def build_denied_trace_for_block(spec: BlockSpec) -> ToolTrace:
    """Build ToolTrace for a blocked policy/guardrail envelope."""
    return build_tool_trace(
        trace_spec_denied(
            index=spec.index,
            call=spec.call,
            manifest=spec.manifest,
            summary=spec.reason,
            error_code=spec.code,
        )
    )
