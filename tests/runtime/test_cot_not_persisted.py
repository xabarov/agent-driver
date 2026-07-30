"""Epic 043 A (integration): inline CoT is stripped from replayable history."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.single_agent.tool_stage import _update_tool_protocol_messages
from agent_driver.runtime.tools import ToolExecutionResult


def test_inline_think_stripped_from_assistant_protocol_checkpoint() -> None:
    llm_response = LlmResponse(
        message=ChatMessage(
            role=ChatRole.ASSISTANT,
            content="<think>secret chain of thought</think>Calling the tool now.",
        ),
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
    context = SimpleNamespace(
        llm_response=llm_response,
        run_input=SimpleNamespace(messages=(), input="hello"),
        metadata={},
    )

    _update_tool_protocol_messages(
        context=context, result=ToolExecutionResult(envelopes=[], traces=[])
    )

    rows = context.metadata["protocol_messages"]
    assistant_rows = [r for r in rows if r.get("role") == ChatRole.ASSISTANT.value]
    assert assistant_rows
    checkpoint = assistant_rows[-1]
    # The chain-of-thought must not survive into replayable history...
    assert "<think>" not in checkpoint["content"]
    assert "secret chain of thought" not in checkpoint["content"]
    # ...while the real, non-reasoning text is preserved.
    assert "Calling the tool now." in checkpoint["content"]
    assert checkpoint["metadata"].get("inline_reasoning_stripped_chars", 0) > 0
