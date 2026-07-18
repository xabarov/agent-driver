"""Tool-failure streak guard (epic 019 phase B)."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import ToolCall, ToolError
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.runtime.single_agent.tool_stage import (
    _force_final_reason,
    _update_tool_failure_guard,
)
from agent_driver.runtime.tools import ToolExecutionResult


class _Host:
    def __init__(self) -> None:
        self.events = []

    def _emit(self, event) -> None:
        self.events.append(event)


def _context():
    return SimpleNamespace(
        run_input=SimpleNamespace(
            max_tool_calls=None,
            max_steps=None,
            tool_policy=SimpleNamespace(metadata={}),
            app_metadata={},
            input="q",
        ),
        metadata={},
        tool_calls=1,
        llm_step_count=1,
        run_id="run_guard",
        attempt_id="att_1",
    )


def _result(
    *, error_code: str | None, tool_name: str = "web_search"
) -> ToolExecutionResult:
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name=tool_name, tool_call_id="call_1", args={}),
        error=ToolError(code=error_code, message="boom") if error_code else None,
        summary=None if error_code else "ok",
    )
    return ToolExecutionResult(envelopes=[envelope])


def test_streak_warns_then_forces_final():
    host = _Host()
    context = _context()
    _update_tool_failure_guard(host, context, _result(error_code="io_error"))
    assert context.metadata["tool_failure_guard"]["count"] == 1
    assert _force_final_reason(context) is None
    _update_tool_failure_guard(host, context, _result(error_code="io_error"))
    assert any(
        e.payload.get("signal_id") == "tool_failure_streak_warning" for e in host.events
    )
    assert _force_final_reason(context) is None  # warn-before-stop: not yet forced
    _update_tool_failure_guard(host, context, _result(error_code="io_error"))
    assert any(
        e.payload.get("signal_id") == "tool_failure_streak_force_final"
        for e in host.events
    )
    assert _force_final_reason(context) == "tool_failure_streak"


def test_streak_resets_on_success_and_on_signature_change():
    host = _Host()
    context = _context()
    _update_tool_failure_guard(host, context, _result(error_code="io_error"))
    _update_tool_failure_guard(host, context, _result(error_code="io_error"))
    # Different error code → new signature, streak restarts at 1.
    _update_tool_failure_guard(host, context, _result(error_code="timeout"))
    assert context.metadata["tool_failure_guard"]["count"] == 1
    # Successful round breaks the streak entirely.
    _update_tool_failure_guard(host, context, _result(error_code=None))
    assert context.metadata["tool_failure_guard"]["count"] == 0
    assert _force_final_reason(context) is None
