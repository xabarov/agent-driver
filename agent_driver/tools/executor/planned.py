"""Parse planned tool calls from LLM response metadata."""

from __future__ import annotations

from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.tool_call_parser import extract_text_form_tool_calls


def dedupe_tool_call_ids(calls: list[ToolCall]) -> list[ToolCall]:
    """Rename colliding tool_call_ids deterministically (epic 042 A).

    Some providers reuse the same ``tool_call_id`` for more than one call in a
    single batch. Downstream, tool-result rows are keyed by id, so a collision
    silently drops the second call's result from every replay. Rename the nth
    occurrence of an id to ``<id>_d<n>`` (n>=1) in call order — deterministic, so
    the prompt-cache prefix stays stable. ``None`` ids are left alone (they get
    unique positional ids later); only real string collisions are renamed.
    """
    seen: dict[str, int] = {}
    result: list[ToolCall] = []
    for call in calls:
        cid = call.tool_call_id
        if isinstance(cid, str) and cid:
            count = seen.get(cid, 0)
            seen[cid] = count + 1
            if count:
                result.append(call.model_copy(update={"tool_call_id": f"{cid}_d{count}"}))
                continue
        result.append(call)
    return result


def extract_planned_tool_calls(llm_response: LlmResponse) -> list[ToolCall]:
    """Parse planned tool calls from LLM response metadata."""
    payload = llm_response.metadata.get("planned_tool_calls")
    if not isinstance(payload, list):
        payload = []
    if (
        not payload
        and llm_response.message.content
        and llm_response.metadata.get("suppress_text_form_tool_calls") is not True
    ):
        parsed_payload, _ = extract_text_form_tool_calls(llm_response.message.content)
        payload = parsed_payload
    calls: list[ToolCall] = []
    for item in payload:
        if isinstance(item, dict):
            calls.append(ToolCall.model_validate(item))
    # Epic 042 C: a call whose arguments were repaired from truncated JSON is only
    # trustworthy when the provider actually finished the turn. A stream that ended
    # with no terminal reason (UNKNOWN) is a transport cut — refuse to execute a
    # synthesized-complete tool call; drop it so the loop re-prompts instead of
    # running half a command.
    if llm_response.finish_reason == LlmFinishReason.UNKNOWN:
        calls = [
            call
            for call in calls
            if not (
                isinstance(call.metadata, dict)
                and call.metadata.get("text_form_args_repaired") is True
            )
        ]
    # Epic 042 A: colliding provider ids must not drop the second result.
    return dedupe_tool_call_ids(calls)
