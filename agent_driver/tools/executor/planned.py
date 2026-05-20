"""Parse planned tool calls from LLM response metadata."""

from __future__ import annotations

from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmResponse
from agent_driver.llm.tool_call_parser import extract_text_form_tool_calls


def extract_planned_tool_calls(llm_response: LlmResponse) -> list[ToolCall]:
    """Parse planned tool calls from LLM response metadata."""
    payload = llm_response.metadata.get("planned_tool_calls")
    if not isinstance(payload, list):
        payload = []
    if not payload and llm_response.message.content:
        parsed_payload, _ = extract_text_form_tool_calls(llm_response.message.content)
        payload = parsed_payload
    calls: list[ToolCall] = []
    for item in payload:
        if isinstance(item, dict):
            calls.append(ToolCall.model_validate(item))
    return calls
