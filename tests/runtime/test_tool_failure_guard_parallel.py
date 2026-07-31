"""Epic 019(b): a parallel fan-out of the same failure counts once per turn."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import ToolCall, ToolError
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.runtime.single_agent.tool_stage import _update_tool_failure_guard
from agent_driver.runtime.tools import ToolExecutionResult


class _Host:
    def __init__(self) -> None:
        self.events = []

    def _emit(self, event) -> None:
        self.events.append(event)


def _context():
    return SimpleNamespace(
        run_input=SimpleNamespace(app_metadata={}, input="q"),
        metadata={},
        run_id="run_guard_parallel",
        attempt_id="att_1",
    )


def _parallel_same_error(n: int, *, code: str = "tool_handler_error") -> ToolExecutionResult:
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_search", tool_call_id=f"c{i}", args={}),
                error=ToolError(code=code, message="boom"),
            )
            for i in range(n)
        ]
    )


def test_parallel_fanout_counts_once_per_turn() -> None:
    host, context = _Host(), _context()
    # One turn with FIVE parallel same-signature failures must advance the streak by 1,
    # not 5 — otherwise the threshold-3 force-final trips before the model adapts.
    _update_tool_failure_guard(host, context, _parallel_same_error(5))
    assert context.metadata["tool_failure_guard"]["count"] == 1
    # No warning/force-final on the first turn.
    assert host.events == []


def test_streak_still_accumulates_across_turns() -> None:
    host, context = _Host(), _context()
    for _ in range(3):
        _update_tool_failure_guard(host, context, _parallel_same_error(4))
    # 3 turns, each a parallel fan-out → count 3 (turns without adaptation), threshold hit.
    assert context.metadata["tool_failure_guard"]["count"] == 3
    signals = [e for e in host.events]
    assert signals  # warned/forced across turns, driven by turns not events


def test_successful_round_resets_streak() -> None:
    host, context = _Host(), _context()
    _update_tool_failure_guard(host, context, _parallel_same_error(3))
    ok = ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_search", tool_call_id="c1", args={}),
                summary="ok",
            )
        ]
    )
    _update_tool_failure_guard(host, context, ok)
    assert context.metadata["tool_failure_guard"] == {"signature": None, "count": 0}
