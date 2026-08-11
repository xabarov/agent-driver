"""F5: a model-facing self-correction nudge fires one turn before the tool-failure
guard hard-forces the final answer."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts import ToolCall, ToolError
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolResultEnvelope
from agent_driver.runtime.single_agent.tool_stage import _update_tool_failure_guard
from agent_driver.runtime.single_agent.tool_stage.recovery import (
    _append_tool_failure_streak_nudge,
)
from agent_driver.runtime.tools import ToolExecutionResult


class _Host:
    def __init__(self) -> None:
        self.events: list = []

    def _emit(self, event) -> None:  # noqa: ANN001
        self.events.append(event)


def _context():
    return SimpleNamespace(
        run_input=SimpleNamespace(app_metadata={}, input="q"),
        metadata={},
        run_id="run_nudge",
        attempt_id="att_1",
    )


def _same_error(code: str = "tool_handler_error") -> ToolExecutionResult:
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="web_search", tool_call_id="c0", args={}),
                error=ToolError(code=code, message="boom"),
            )
        ]
    )


def test_guard_marks_nudge_due_one_turn_before_force_final() -> None:
    host, context = _Host(), _context()
    _update_tool_failure_guard(host, context, _same_error())  # count 1
    assert "tool_failure_nudge_due" not in context.metadata
    _update_tool_failure_guard(host, context, _same_error())  # count 2 == threshold-1
    assert context.metadata["tool_failure_nudge_due"] == "web_search:tool_handler_error"


def test_nudge_appends_one_model_facing_message_and_consumes_the_flag() -> None:
    context = _context()
    context.metadata["tool_failure_nudge_due"] = "web_search:tool_handler_error"
    messages: list[ChatMessage] = []

    _append_tool_failure_streak_nudge(context, _same_error(), messages)

    assert len(messages) == 1
    assert messages[0].role == ChatRole.USER
    assert "web_search" in messages[0].content
    assert "tool_handler_error" in messages[0].content
    assert context.metadata["tool_failure_nudge_sent"] == "web_search:tool_handler_error"
    assert "tool_failure_nudge_due" not in context.metadata  # consumed


def test_nudge_is_deduped_per_signature() -> None:
    context = _context()
    context.metadata["tool_failure_nudge_sent"] = "web_search:tool_handler_error"
    context.metadata["tool_failure_nudge_due"] = "web_search:tool_handler_error"
    messages: list[ChatMessage] = []

    _append_tool_failure_streak_nudge(context, _same_error(), messages)
    assert messages == []  # already nudged for this signature


def test_nudge_noop_without_due_flag() -> None:
    context = _context()
    messages: list[ChatMessage] = []
    _append_tool_failure_streak_nudge(context, _same_error(), messages)
    assert messages == []
