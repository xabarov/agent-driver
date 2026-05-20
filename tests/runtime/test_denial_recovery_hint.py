"""Tests for denial recovery hint insertion into protocol messages."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall, ToolError, ToolResultEnvelope
from agent_driver.runtime.single_agent.tool_stage import _append_denial_recovery_message
from agent_driver.runtime.tools import ToolExecutionResult


def _denied_result(message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name="bash", args={"command": "echo hi; pwd"}),
                decision=ToolPolicyDecision.DENY,
                error=ToolError(code="tool_handler_error", message=message, retryable=True),
            )
        ]
    )


def test_denial_recovery_hint_added_for_tool_handler_error() -> None:
    """Runtime should append corrective user message after handler denial."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]
    _append_denial_recovery_message(
        context,
        _denied_result("statement separator ';' is not allowed"),
        messages,
    )
    assert len(messages) == 2
    assert "Tool 'bash' was denied" in (messages[-1].content or "")
    assert "do not repeat the same denied call" in (messages[-1].content or "")


def test_denial_recovery_hint_is_deduplicated_for_repeated_denial() -> None:
    """Second identical denial should not insert duplicate corrective message."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]
    result = _denied_result("statement separator ';' is not allowed")
    _append_denial_recovery_message(context, result, messages)
    _append_denial_recovery_message(context, result, messages)
    assert len(messages) == 2


def test_denial_recovery_forces_final_after_second_handler_error() -> None:
    """Second handler denial for same tool should force final answer mode."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]
    _append_denial_recovery_message(context, _denied_result("denied_once"), messages)
    _append_denial_recovery_message(context, _denied_result("denied_twice"), messages)
    assert context.metadata.get("force_final_answer") is True
    assert context.metadata.get("tool_choice_override") == "none"
    assert "failed twice" in (messages[-1].content or "")
