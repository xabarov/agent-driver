"""Translate between the OpenAI chat surface and the runtime vocabulary.

Inbound: an OpenAI :class:`ChatCompletionRequest` -> :class:`AgentRunInput`.
Outbound: a final :class:`AgentRunOutput` -> a ``chat.completion`` object, and a
streamed answer -> a sequence of ``chat.completion.chunk`` frames. These
functions are pure (no I/O) so the wire shapes are unit-testable offline.
"""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput, AgentRunOutput
from agent_driver.server.openai.schema import ChatCompletionRequest, ChatMessageIn
from agent_driver.server.usage import chat_usage

# OpenAI roles we accept -> runtime ChatRole values. Unknown roles fall back to
# "user" so a stray role never drops a message.
_ROLE_MAP = {
    "system": "system",
    "developer": "system",
    "user": "user",
    "assistant": "assistant",
    "tool": "tool",
    "function": "tool",
}


def to_chat_messages(messages: list[ChatMessageIn]) -> list[ChatMessage]:
    """Convert inbound OpenAI messages to runtime ``ChatMessage`` objects."""
    converted: list[ChatMessage] = []
    for message in messages:
        role = _ROLE_MAP.get(message.role, "user")
        attachments = message.media_attachments()
        metadata = {"attachments": attachments} if attachments else {}
        converted.append(
            ChatMessage(role=role, content=message.text_content(), metadata=metadata)
        )
    return converted


def to_run_input(
    request: ChatCompletionRequest,
    *,
    run_id: str,
    agent_id: str,
    graph_preset: str,
    thread_id: str | None = None,
    history: list[ChatMessage] | None = None,
) -> AgentRunInput:
    """Build an ``AgentRunInput`` from a chat-completions request.

    ``history`` (server-side session memory) is prepended to the request's
    messages; in the stateless case it is empty and the request's ``messages``
    are authoritative (the client resends the full conversation).
    """
    messages = list(history or []) + to_chat_messages(request.messages)
    return AgentRunInput(
        messages=messages,
        run_id=run_id,
        thread_id=thread_id,
        agent_id=agent_id,
        graph_preset=graph_preset,
        stream=request.stream,
        response_format=request.response_format,
        temperature=request.temperature,
        max_tokens=request.max_tokens,
        app_metadata={"openai_model": request.model},
    )


def usage_dict(output: AgentRunOutput) -> dict[str, int]:
    """Project the run's usage onto OpenAI chat ``usage`` fields (zeros if absent)."""
    return chat_usage(output)


def _finish_reason(output: AgentRunOutput) -> str:
    """Map a terminal run status to an OpenAI finish reason."""
    status = getattr(output.status, "value", output.status)
    if status == "completed":
        return "stop"
    if status in ("timed_out",):
        return "length"
    return "stop"


def completion_id(run_id: str) -> str:
    """Stable chat-completion id derived from the run id."""
    return f"chatcmpl-{run_id}"


def completion_object(
    output: AgentRunOutput, *, model: str, created: int
) -> dict[str, Any]:
    """Assemble a non-streaming ``chat.completion`` object."""
    return {
        "id": completion_id(output.run_id),
        "object": "chat.completion",
        "created": created,
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": output.answer or "",
                },
                "finish_reason": _finish_reason(output),
            }
        ],
        "usage": usage_dict(output),
    }


def role_chunk(run_id: str, *, model: str, created: int) -> dict[str, Any]:
    """First streaming chunk: announces the assistant role."""
    return _chunk(run_id, model=model, created=created, delta={"role": "assistant"})


def content_chunk(
    run_id: str, text: str, *, model: str, created: int
) -> dict[str, Any]:
    """A streaming chunk carrying a token delta."""
    return _chunk(run_id, model=model, created=created, delta={"content": text})


def final_chunk(output: AgentRunOutput, *, model: str, created: int) -> dict[str, Any]:
    """Terminal streaming chunk: empty delta + finish_reason."""
    return _chunk(
        output.run_id,
        model=model,
        created=created,
        delta={},
        finish_reason=_finish_reason(output),
    )


def usage_chunk(output: AgentRunOutput, *, model: str, created: int) -> dict[str, Any]:
    """Final streaming chunk carrying ``usage`` (OpenAI stream_options).

    Per the OpenAI contract the usage chunk has an empty ``choices`` array and a
    populated ``usage`` object; it is sent after the finish-reason chunk.
    """
    return {
        "id": completion_id(output.run_id),
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [],
        "usage": usage_dict(output),
    }


def _chunk(
    run_id: str,
    *,
    model: str,
    created: int,
    delta: dict[str, Any],
    finish_reason: str | None = None,
) -> dict[str, Any]:
    return {
        "id": completion_id(run_id),
        "object": "chat.completion.chunk",
        "created": created,
        "model": model,
        "choices": [{"index": 0, "delta": delta, "finish_reason": finish_reason}],
    }


__all__ = [
    "to_chat_messages",
    "to_run_input",
    "completion_id",
    "completion_object",
    "usage_dict",
    "role_chunk",
    "content_chunk",
    "final_chunk",
    "usage_chunk",
]
