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


def test_update_tool_protocol_messages_adds_web_fetch_verification_hint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="call_w1",
                    args={"query": "sam3"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="web_search", tool_call_id="call_w1", args={"query": "sam3"}),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={
            "summary": "2 results for 'sam3'",
            "results": [{"title": "SAM3", "url": "https://ai.meta.com/blog/segment-anything-model-3/"}],
            "result_preview_urls": ["https://ai.meta.com/blog/segment-anything-model-3/"],
        },
        truncated=False,
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
    assert payload["result_preview_urls"] == ["https://ai.meta.com/blog/segment-anything-model-3/"]
    user_rows = [row for row in rows if row.get("role") == ChatRole.USER.value]
    assert any("open at least one returned URL with web_fetch" in (item.get("content") or "") for item in user_rows)


def test_update_tool_protocol_messages_coalesces_user_hints_with_force_final() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="file_write",
                    tool_call_id="call_d1",
                    args={"path": "/etc/passwd", "content": "x"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(
            tool_name="file_write",
            tool_call_id="call_d1",
            args={"path": "/etc/passwd", "content": "x"},
        ),
        decision=ToolPolicyDecision.DENY,
        structured_output={},
        truncated=False,
        error=ToolError(code="tool_handler_error", message="policy denied"),
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={"force_final_answer": True},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    user_rows = [row for row in rows if row.get("role") == ChatRole.USER.value]
    assert len(user_rows) == 2
    content = user_rows[-1].get("content") or ""
    assert "was denied" in content
    assert "Do not call additional tools unless absolutely required" in content


def test_update_tool_protocol_messages_drops_empty_assistant_before_user_hint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content="<tool_call>{}</tool_call>"),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="fake"),
        provider="fake",
        model="fake",
        metadata={
            "planned_tool_calls": [
                ToolCall(
                    tool_name="web_search",
                    tool_call_id="call_w2",
                    args={"query": "sam3"},
                ).model_dump(mode="json")
            ]
        },
    )
    envelope = ToolResultEnvelope(
        call=ToolCall(tool_name="web_search", tool_call_id="call_w2", args={"query": "sam3"}),
        decision=ToolPolicyDecision.ALLOW,
        structured_output={"results": [], "summary": "no results"},
        truncated=False,
    )
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={"force_final_answer": True},
    )
    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[envelope], traces=[])
    )
    rows = context.metadata["protocol_messages"]
    assert not any(
        row.get("role") == ChatRole.ASSISTANT.value
        and not (row.get("content") or "").strip()
        and not (
            isinstance(row.get("metadata"), dict) and row.get("metadata", {}).get("tool_calls")
        )
        for row in rows
    )
