"""Tests for tool protocol message payload shaping."""

from __future__ import annotations

import json
from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole, ToolPolicyDecision
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall, ToolError, ToolResultEnvelope
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.single_agent.tool_stage import _update_tool_protocol_messages
from agent_driver.runtime.tools import ToolExecutionResult


def test_update_tool_protocol_messages_includes_truncated_and_error_code() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="glob_search",
                    tool_call_id="call_1",
                    args={"pattern": "**/*"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="glob_search", tool_call_id="call_1", args={"pattern": "**/*"}),
        decision=ToolPolicyDecision.DENY,
        structured_output={"summary": "cap hit", "results": ["a.py"]},
        truncated=True,
        error=ToolError(code="tool_policy_denied", message="denied"),
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    tool_rows = [row for row in rows if row.get("role") == ChatRole.TOOL.value]
    assert tool_rows
    payload = json.loads(tool_rows[-1]["content"])
    assert payload["truncated"] is True
    assert payload["error_code"] == "tool_policy_denied"
