from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.payload_debug import summarize_llm_request_payload


def test_payload_debug_summarizes_reasoning_without_content() -> None:
    summary = summarize_llm_request_payload(
        LlmRequest(
            messages=[
                ChatMessage(role=ChatRole.USER, content="hello"),
                ChatMessage(
                    role=ChatRole.ASSISTANT,
                    content="",
                    metadata={
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {"name": "web_fetch", "arguments": "{}"},
                            }
                        ],
                        "reasoning_details": [
                            {"type": "reasoning.summary", "summary": "secret"}
                        ],
                    },
                ),
            ],
            tools=[
                {
                    "type": "function",
                    "function": {"name": "web_fetch", "parameters": {}},
                }
            ],
            tool_choice={"type": "tool", "name": "web_fetch"},
        )
    )

    assert summary["assistant_reasoning_detail_counts"] == [1]
    assert summary["tool_call_ids"] == ["call_1"]
    assert summary["tool_names"] == ["web_fetch"]
    assert "secret" not in str(summary)
