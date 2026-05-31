"""Streaming recovery and forced-final event helpers for LLM-call step."""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import urlparse

from agent_driver.contracts.enums import ChatRole, RuntimeEventType
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.prompts import force_final_answer_user_message
from agent_driver.runtime.metadata_state import (
    get_streaming_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.research_evidence import RESEARCH_DEPTH_SOURCE_VERIFIED
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.llm_step.streaming import emit_token_delta_events
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class StreamRecoveryHost(Protocol):
    """Host surface required for streaming recovery events."""

    _deps: Any

    def _emit(self, event: EventSpec) -> None: ...


def force_final_answer_message(context: RunContext) -> str:
    """Return force-final message, enriched with fetched source links."""
    message = force_final_answer_user_message()
    source_links = fetched_source_links(context)
    if not source_links:
        return message
    bullets = "\n".join(f"- {title}: {url}" for title, url in source_links[:5])
    return (
        f"{message}\n\n"
        "You used fetched web sources. Include concrete Markdown links in the "
        "final answer and base the synthesis on these URLs:\n"
        f"{bullets}"
    )


def fetched_source_links(context: RunContext) -> list[tuple[str, str]]:
    """Return fetched web sources relevant to a source-verified report."""
    task_contract = context.run_input.tool_policy.metadata.get("task_contract")
    if not (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_SOURCE_VERIFIED
    ):
        return []
    tool_results = get_tool_loop_state(context).tool_results()
    links: list[tuple[str, str]] = []
    seen: set[str] = set()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "web_fetch":
            continue
        url = tool_result_url(item)
        if not url or url in seen:
            continue
        seen.add(url)
        links.append((source_label(item, url), url))
    return links


def tool_result_url(item: dict[str, Any]) -> str | None:
    """Extract URL from a web_fetch tool result row."""
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        url = structured.get("url")
        if isinstance(url, str) and url:
            return url
    call = item.get("call")
    if isinstance(call, dict):
        args = call.get("args")
        if isinstance(args, dict):
            url = args.get("url")
            if isinstance(url, str) and url:
                return url
    return None


def source_label(item: dict[str, Any], url: str) -> str:
    """Return compact source label for a fetched URL."""
    structured = item.get("structured_output")
    if isinstance(structured, dict):
        metadata = structured.get("metadata")
        if isinstance(metadata, dict):
            title = metadata.get("title")
            if isinstance(title, str) and title.strip():
                return title.strip()
    domain = urlparse(url).netloc.lower()
    return domain[4:] if domain.startswith("www.") else domain or "source"


def emit_partial_assistant_tombstone(
    host: StreamRecoveryHost,
    context: RunContext,
    *,
    reason: str,
) -> None:
    """Mark partial streamed assistant output as invalid before terminal failure."""
    streaming_state = get_streaming_runtime_state(context)
    if not streaming_state.started():
        return
    if streaming_state.completed():
        return
    content = streaming_state.content()
    if not isinstance(content, str) or not content:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_TOMBSTONED,
        payload={
            "reason": reason,
            "content": content,
            "transition_reason": "partial_tombstone",
        },
    )
    streaming_state.mark_tombstoned()


def recover_force_final_stream_response(
    host: StreamRecoveryHost,
    context: RunContext,
    *,
    reason: str,
) -> LlmResponse | None:
    """Preserve a late-stream final answer when provider transport drops."""
    streaming_state = get_streaming_runtime_state(context)
    if not get_tool_loop_state(context).force_final_answer_enabled():
        return None
    if streaming_state.completed():
        return None
    content = streaming_state.content()
    if not isinstance(content, str) or len(content.strip()) < 200:
        return None
    streaming_state.mark_recovered(content=content, reason=reason)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.WARNING,
        payload={
            "warning": "Recovered partial final answer after provider stream error.",
            "signal_id": "provider_stream_partial_final_recovered",
            "severity": "warning",
            "transition_reason": reason,
            "chars": len(content),
        },
    )
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED,
        payload={
            "content": content,
            "finish_reason": LlmFinishReason.UNKNOWN.value,
            "provider": host._deps.provider.name,
            "model": "stream-model",
            "recovered_partial": True,
            "transition_reason": reason,
        },
    )
    return LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content=content),
        finish_reason=LlmFinishReason.UNKNOWN,
        provider=host._deps.provider.name,
        model="stream-model",
        metadata={
            "token_chunks_emitted": True,
            "provider_stream_partial_final_recovered": True,
            "transition_reason": reason,
        },
    )


def should_retry_empty_forced_final_non_stream(
    context: RunContext, response: LlmResponse
) -> bool:
    """Return whether empty forced-final stream should retry non-streaming."""
    metadata = getattr(context, "metadata", {})
    if not isinstance(metadata, dict) or metadata.get("force_final_answer") is not True:
        return False
    if response.finish_reason != LlmFinishReason.STOP:
        return False
    if (response.message.content or "").strip():
        return False
    planned = response.metadata.get("planned_tool_calls")
    return not isinstance(planned, list) or not planned


def forced_final_no_tools_retry_reason(
    context: RunContext,
    request: Any,
    response: LlmResponse,
) -> str | None:
    """Return why forced-final response should be retried with tools disabled."""
    metadata = getattr(context, "metadata", {})
    if not isinstance(metadata, dict) or metadata.get("force_final_answer") is not True:
        return None
    if not isinstance(request, LlmRequest):
        return None
    if not request.tools and request.tool_choice is None:
        return None
    if response.metadata.get("text_form_tool_calls_suppressed") is True:
        return "tool_call"
    planned = response.metadata.get("planned_tool_calls")
    if isinstance(planned, list) and planned:
        return "tool_call"
    if response.finish_reason != LlmFinishReason.STOP:
        return None
    if (response.message.content or "").strip():
        return None
    return "empty" if not isinstance(planned, list) or not planned else None


def emit_non_stream_retry_assistant_message(
    host: StreamRecoveryHost,
    context: RunContext,
    response: LlmResponse,
) -> None:
    """Emit replacement events for a successful no-tools non-stream retry."""
    content = (response.message.content or "").strip()
    if not content:
        return
    emit_token_delta_events(host, context, [content])
    get_streaming_runtime_state(context).mark_completed(content)
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.ASSISTANT_MESSAGE_REPLACED,
        payload={
            "content": content,
            "finish_reason": response.finish_reason.value,
            "provider": response.provider,
            "model": response.model,
            "replacement_reason": "empty_forced_final_no_tools_retry",
        },
    )


__all__ = [
    "emit_non_stream_retry_assistant_message",
    "emit_partial_assistant_tombstone",
    "fetched_source_links",
    "force_final_answer_message",
    "forced_final_no_tools_retry_reason",
    "recover_force_final_stream_response",
    "should_retry_empty_forced_final_non_stream",
    "source_label",
    "tool_result_url",
]
