"""Streaming chat and resume endpoints."""

from __future__ import annotations

from collections.abc import Iterable
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_driver.contracts import AgentRunInput, ChatMessage
from agent_driver.contracts.enums import ChatRole, ResumeAction
from agent_driver.contracts.interrupts import InterruptRequest, ResumeCommand
from agent_driver.runtime.stream import project_runtime_events

from app.config import Settings, ToolPreset
from app.deps import get_agent_bundle, get_agent_bundle_for_preset, get_settings, resolve_tool_preset
from app.schemas.chat import ChatMessageRequest, InterruptView, ResumeRequest
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


def _bundle_for_preset(preset: ToolPreset) -> AgentBundle:
    return get_agent_bundle_for_preset(preset)


def get_resume_bundle(body: ResumeRequest) -> AgentBundle:
    """Resolve agent bundle for resume (injectable in tests)."""
    return get_agent_bundle_for_preset(resolve_tool_preset(body.tool_preset))


def _parse_interrupt_payload(payload: object) -> InterruptRequest | None:
    if not isinstance(payload, dict):
        return None
    try:
        return InterruptRequest.model_validate(payload)
    except Exception:
        return None


def _interrupt_from_checkpoint(bundle: AgentBundle, run_id: str) -> InterruptRequest:
    record = bundle.checkpoint_store.latest(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    latest_output = record.state.latest_output
    if latest_output is not None and latest_output.interrupt is not None:
        return latest_output.interrupt
    metadata = record.state.metadata
    interrupt = _parse_interrupt_payload(metadata.get("interrupt_payload"))
    if interrupt is None:
        pending = metadata.get("pending_interrupt")
        if isinstance(pending, dict) and isinstance(pending.get("interrupt"), dict):
            interrupt = _parse_interrupt_payload(pending["interrupt"])
    if interrupt is None:
        raise HTTPException(status_code=404, detail="interrupt not found")
    return interrupt


@router.post("/chat/messages")
async def chat_messages(
    body: ChatMessageRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Start one run and stream normalized runtime events as SSE."""
    preset = resolve_tool_preset(body.tool_preset)
    bundle = _bundle_for_preset(preset)
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


@router.get("/chat/runs/{run_id}/interrupt", response_model=InterruptView)
def get_run_interrupt(
    run_id: str,
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> InterruptView:
    """Return pending interrupt metadata for a paused run."""
    interrupt = _interrupt_from_checkpoint(bundle, run_id)
    return InterruptView(
        run_id=run_id,
        interrupt_id=interrupt.interrupt_id,
        reason=interrupt.reason.value,
        title=interrupt.title,
        description=interrupt.description,
        proposed_action=dict(interrupt.proposed_action),
        allowed_actions=[action.value for action in interrupt.allowed_actions],
    )


@router.post("/chat/runs/{run_id}/resume")
async def resume_run(
    run_id: str,
    body: ResumeRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
    bundle: AgentBundle = Depends(get_resume_bundle),
) -> StreamingResponse:
    """Resume a paused run and stream remaining events as SSE."""
    record = bundle.checkpoint_store.latest(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail="checkpoint not found")
    try:
        action = ResumeAction(body.action)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid resume action") from exc

    base_input = record.state.run_input
    run_input = base_input.model_copy(
        update={
            "stream": True,
            "resume": ResumeCommand(
                interrupt_id=body.interrupt_id,
                action=action,
                edited_tool_args=body.edited_tool_args,
                message=body.message,
            ),
            "app_metadata": {
                **dict(base_input.app_metadata),
                "stream_poll_interval_ms": settings.stream_poll_interval_ms,
            },
        }
    )

    stream = relay_and_capture(
        agent=bundle.agent,
        run_input=run_input,
        event_log=bundle.event_log,
        last_event_id=request.headers.get("Last-Event-ID"),
        on_finish=None,
    )
    return StreamingResponse(
        stream,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Run-Id": run_id,
        },
    )


def replay_events_for_run(bundle: AgentBundle, run_id: str) -> list[dict[str, object]]:
    """Load projected stream events for one run."""
    events = project_runtime_events(bundle.event_log.list_for_run(run_id))
    return [event.model_dump(mode="json") for event in events]
