"""Streaming chat endpoint."""

from __future__ import annotations

from collections.abc import Iterable
import uuid

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from agent_driver.contracts import AgentRunInput, ChatMessage
from agent_driver.contracts.enums import ChatRole

from app.config import Settings
from app.deps import get_agent_bundle, get_settings
from app.schemas.chat import ChatMessageRequest
from app.services.agent_factory import AgentBundle
from app.sse_relay import relay_and_capture

router = APIRouter(tags=["chat"])


def _transcript_to_messages(transcript: Iterable[tuple[str, str]]) -> list[ChatMessage]:
    messages: list[ChatMessage] = []
    for role, content in transcript:
        if not content.strip():
            continue
        if role == "user":
            messages.append(ChatMessage(role=ChatRole.USER, content=content))
        elif role == "assistant":
            messages.append(ChatMessage(role=ChatRole.ASSISTANT, content=content))
        elif role == "system":
            messages.append(ChatMessage(role=ChatRole.SYSTEM, content=content))
    return messages


@router.post("/chat/messages")
async def chat_messages(
    body: ChatMessageRequest,
    request: Request,
    bundle: AgentBundle = Depends(get_agent_bundle),
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Start one run and stream normalized runtime events as SSE."""
    session_id = body.session_id or f"session_{uuid.uuid4().hex[:8]}"
    record = bundle.session_store.get(session_id)
    if record is None:
        thread_id = f"thread_{uuid.uuid4().hex[:8]}"
        run_ids: list[str] = []
        transcript: list[tuple[str, str]] = []
    else:
        thread_id = record.thread_id
        run_ids = list(record.run_ids)
        transcript = list(record.transcript)

    transcript.append(("user", body.message))
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_ids.append(run_id)
    bundle.session_store.upsert(
        session_id=session_id,
        thread_id=thread_id,
        run_ids=run_ids,
        transcript=transcript,
    )
    run_input = AgentRunInput(
        input=body.message,
        messages=_transcript_to_messages(transcript),
        run_id=run_id,
        thread_id=thread_id,
        agent_id="chat-demo-agent",
        graph_preset="single_react",
        stream=True,
        max_steps=settings.max_steps,
        max_tool_calls=settings.max_tool_calls,
        deadline_seconds=settings.deadline_seconds,
        app_metadata={"stream_poll_interval_ms": settings.stream_poll_interval_ms},
    )

    def _persist_assistant(assistant_text: str, _terminal_event: str | None) -> None:
        next_transcript = list(transcript)
        if assistant_text.strip():
            next_transcript.append(("assistant", assistant_text))
        bundle.session_store.upsert(
            session_id=session_id,
            thread_id=thread_id,
            run_ids=run_ids,
            transcript=next_transcript,
        )

    stream = relay_and_capture(
        agent=bundle.agent,
        run_input=run_input,
        event_log=bundle.event_log,
        last_event_id=request.headers.get("Last-Event-ID"),
        on_finish=_persist_assistant,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Session-Id": session_id,
            "X-Run-Id": run_id,
        },
    )

