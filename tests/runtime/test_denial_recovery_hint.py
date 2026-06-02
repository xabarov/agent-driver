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


def _policy_denied_result(*, tool_name: str, message: str) -> ToolExecutionResult:
    return ToolExecutionResult(
        envelopes=[
            ToolResultEnvelope(
                call=ToolCall(tool_name=tool_name, args={}),
                decision=ToolPolicyDecision.DENY,
                error=ToolError(code="policy_denied", message=message, retryable=True),
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


def test_initial_subagent_gate_denial_forces_agent_tool_recovery() -> None:
    """Medium/hard Deep Research should recover denied web search into agent_tool."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]

    _append_denial_recovery_message(
        context,
        _policy_denied_result(
            tool_name="web_search",
            message=(
                "deep_research_initial_subagent_gate denied 'web_search': "
                "medium/hard Deep Research must first delegate bounded source "
                "discovery with agent_tool before direct web or write tools."
            ),
        ),
        messages,
    )

    assert context.metadata.get("tool_choice_override") == {
        "type": "tool",
        "name": "agent_tool",
    }
    assert context.metadata["deep_research_initial_subagent_recovery"] == {
        "tool": "agent_tool",
        "reason": "initial_subagent_gate_denied",
    }
    assert "Call agent_tool now" in (messages[-1].content or "")
    assert "do not call web_search" in (messages[-1].content or "")


def test_repeated_initial_subagent_gate_denial_reemits_stronger_recovery() -> None:
    """Repeated hidden web-search drift should not be silently deduplicated."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]
    result = _policy_denied_result(
        tool_name="web_search",
        message=(
            "deep_research_initial_subagent_gate denied 'web_search': "
            "medium/hard Deep Research must first delegate bounded source "
            "discovery with agent_tool before direct web or write tools."
        ),
    )

    _append_denial_recovery_message(context, result, messages)
    _append_denial_recovery_message(context, result, messages)

    assert len(messages) == 3
    assert context.metadata["denied_tool_counts"]["web_search"] == 2
    assert context.metadata.get("tool_choice_override") == {
        "type": "tool",
        "name": "agent_tool",
    }
    assert "repeated denied call" in (messages[-1].content or "")
    assert "only agent_tool" in (messages[-1].content or "")


def test_parent_synthesis_gate_denial_forces_file_write_recovery() -> None:
    """After joined child notes, recovery should write, not read artifacts."""
    context = SimpleNamespace(metadata={})
    messages = [ChatMessage(role="user", content="start")]

    _append_denial_recovery_message(
        context,
        _policy_denied_result(
            tool_name="artifact_list",
            message=(
                "deep_research_parent_synthesis_gate denied 'artifact_list': "
                "joined child research notes are pending parent synthesis."
            ),
        ),
        messages,
    )

    assert context.metadata.get("tool_choice_override") == {
        "type": "tool",
        "name": "file_write",
    }
    assert context.metadata["deep_research_parent_synthesis_recovery"] == {
        "tool": "file_write",
        "reason": "parent_synthesis_gate_denied",
    }
    content = messages[-1].content or ""
    assert "call file_write" in content
    assert "Use web_fetch" in content
    assert "research/report.md" in content
    assert "artifact_list" in content
    assert "agent_tool" in content
