"""Streaming chat and resume endpoints."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Iterable
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_driver.adapters import parse_after_seq, render_sse_line
from agent_driver.contracts import (
    AgentRunInput,
    ChatMessage,
    ControlRequest,
    ToolPolicyInput,
)
from agent_driver.contracts.enums import ChatRole, ResumeAction
from agent_driver.contracts.interrupts import InterruptRequest, ResumeCommand
from agent_driver.runtime.planning_policy import classify_planning_hint
from agent_driver.runtime.stream import backfill_stream_events, project_runtime_events

from app.config import Settings, ToolPreset
from app.deps import (
    get_agent_bundle,
    get_agent_bundle_for_request,
    get_settings,
    resolve_tool_preset,
)
from app.run_cancel import is_cancelled, request_cancel
from app.schemas.chat import (
    CancelRunResponse,
    ChatControlRequest,
    ChatControlResponse,
    ChatMessageRequest,
    InterruptView,
    ResumeRequest,
)
from app.services.agent_factory import AgentBundle
from app.services.message_metadata import aggregate_metadata_from_events
from app.sse_relay import ensure_run_task, relay_and_capture
from app.workspace import build_chat_app_metadata, merge_resume_app_metadata

router = APIRouter(tags=["chat"])
_TERMINAL_EVENTS = {
    "interrupt_requested",
    "run_completed",
    "run_failed",
    "run_cancelled",
}


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


def _truncate_for_retry(
    *,
    transcript: list[tuple[str, str]],
    run_ids: list[str],
    retry_from_run_id: str | None,
) -> tuple[list[tuple[str, str]], list[str]]:
    """Drop the retried run and everything after it from persisted chat history."""
    if not retry_from_run_id:
        return transcript, run_ids
    try:
        run_index = run_ids.index(retry_from_run_id)
    except ValueError:
        return transcript, run_ids

    user_seen = 0
    cut_index = len(transcript)
    for index, (role, _content) in enumerate(transcript):
        if role != "user":
            continue
        if user_seen == run_index:
            cut_index = index
            break
        user_seen += 1
    return transcript[:cut_index], run_ids[:run_index]


def _client_requests_dict(record: object | None) -> dict[str, dict[str, object]]:
    rows = getattr(record, "client_requests", ()) if record is not None else ()
    return {str(key): dict(value) for key, value in rows}


def _metadata_by_run_dict(record: object | None) -> dict[str, dict[str, object]]:
    rows = getattr(record, "metadata_by_run", ()) if record is not None else ()
    return {str(key): dict(value) for key, value in rows}


def _filter_client_requests_for_runs(
    client_requests: dict[str, dict[str, object]],
    run_ids: list[str],
) -> dict[str, dict[str, object]]:
    allowed = set(run_ids)
    return {
        key: value
        for key, value in client_requests.items()
        if isinstance(value.get("run_id"), str) and value["run_id"] in allowed
    }


def _session_record_for_run(bundle: AgentBundle, run_id: str):
    for record in bundle.session_store.list_sessions():
        if run_id in record.run_ids:
            return record
    return None


def _persist_steering_history(
    *,
    bundle: AgentBundle,
    run_id: str | None,
    queue_id: str | None,
    control_id: str | None,
    status: str,
) -> None:
    if not run_id or not queue_id:
        return
    record = _session_record_for_run(bundle, run_id)
    if record is None:
        return
    queued = bundle.command_queue_store.get(queue_id)
    metadata_by_run = _metadata_by_run_dict(record)
    run_metadata = dict(metadata_by_run.get(run_id, {}))
    existing_controls = run_metadata.get("steering_controls")
    controls: list[dict[str, object]] = []
    if isinstance(existing_controls, list):
        controls = [
            dict(item) for item in existing_controls if isinstance(item, dict)
        ]
    entry: dict[str, object] = {
        "queue_id": queue_id,
        "status": status,
    }
    if control_id:
        entry["control_id"] = control_id
    if queued is not None:
        entry.update(
            {
                "control_id": queued.control_id,
                "kind": queued.kind.value,
                "priority": queued.priority.value,
                "payload": dict(queued.payload),
                "source": queued.source,
                "created_at": queued.created_at,
                "updated_at": queued.updated_at,
            }
        )
    replaced = False
    for index, item in enumerate(controls):
        if item.get("queue_id") == queue_id:
            controls[index] = {**item, **entry}
            replaced = True
            break
    if not replaced:
        controls.append(entry)
    run_metadata["steering_controls"] = controls
    metadata_by_run[run_id] = run_metadata
    bundle.session_store.upsert(
        session_id=record.session_id,
        thread_id=record.thread_id,
        run_ids=list(record.run_ids),
        transcript=list(record.transcript),
        metadata_by_run=metadata_by_run,
        client_requests=_client_requests_dict(record),
    )


async def _tail_existing_run(
    *,
    bundle: AgentBundle,
    run_id: str,
    request: Request,
    last_event_id: str | None,
    timeout_seconds: float,
    keepalive_seconds: float,
) -> AsyncIterator[str]:
    """Backfill and tail an already reserved run without starting it again."""
    after_seq = parse_after_seq(last_event_id, run_id=run_id) or 0
    started = asyncio.get_running_loop().time()
    keepalive_after = started + keepalive_seconds
    while True:
        saw_event = False
        for event in backfill_stream_events(
            bundle.event_log,
            run_id=run_id,
            after_seq=after_seq,
        ):
            saw_event = True
            after_seq = event.seq
            yield render_sse_line(event)
            if event.event in _TERMINAL_EVENTS:
                return
        if await request.is_disconnected():
            return
        now = asyncio.get_running_loop().time()
        if now - started >= timeout_seconds:
            return
        if keepalive_seconds > 0 and not saw_event and now >= keepalive_after:
            keepalive_after = now + keepalive_seconds
            yield ":keepalive\n\n"
        await asyncio.sleep(0.2)


def _bundle_for_request(preset: ToolPreset, model: str | None = None) -> AgentBundle:
    return get_agent_bundle_for_request(preset, model)


def get_resume_bundle(body: ResumeRequest) -> AgentBundle:
    """Resolve agent bundle for resume (injectable in tests)."""
    return get_agent_bundle_for_request(
        resolve_tool_preset(body.tool_preset),
        body.model,
    )


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


def _is_deliverable_request(message: str) -> bool:
    text = " ".join(message.lower().split())
    markers = (
        "не план",
        "напиши реферат",
        "напиши черновик",
        "связный черновик",
        "финальный ответ",
        "итоговый ответ",
        "write the report",
        "write a report",
        "draft the report",
        "draft an essay",
        "final answer",
        "not a plan",
    )
    return any(marker in text for marker in markers)


def _chat_tool_policy(*, body: ChatMessageRequest, settings: Settings) -> ToolPolicyInput:
    force_planning = (
        body.force_planning
        if body.force_planning is not None
        else settings.force_planning
    )
    metadata: dict[str, object] = {}
    hint = classify_planning_hint(body.message)
    metadata["planning_hint"] = hint.model_dump(mode="json")
    denied_tools: list[str] | None = None
    if _is_deliverable_request(body.message):
        metadata["deliverable_request"] = {
            "enabled": True,
            "reason": "user asked to produce the deliverable now",
        }
        denied_tools = [
            "ask_user_question",
            "enter_plan_mode",
            "exit_plan_mode_v2",
        ]
    if force_planning:
        metadata["force_planning"] = {
            "enabled": True,
            "mode": settings.force_planning_mode,
        }
    return ToolPolicyInput(metadata=metadata, denied_tools=denied_tools)


@router.post("/chat/messages")
async def chat_messages(
    body: ChatMessageRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Start one run and stream normalized runtime events as SSE."""
    preset = resolve_tool_preset(body.tool_preset)
    bundle = _bundle_for_request(preset, body.model)
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
    client_requests = _client_requests_dict(record)
    request_key = body.client_request_id.strip() if body.client_request_id else ""
    if request_key and request_key in client_requests:
        existing_run_id = client_requests[request_key].get("run_id")
        if (
            isinstance(existing_run_id, str)
            and existing_run_id
            and existing_run_id != body.retry_from_run_id
        ):
            return StreamingResponse(
                _tail_existing_run(
                    bundle=bundle,
                    run_id=existing_run_id,
                    request=request,
                    last_event_id=request.headers.get("Last-Event-ID"),
                    timeout_seconds=settings.deadline_seconds,
                    keepalive_seconds=settings.sse_keepalive_seconds,
                ),
                media_type="text/event-stream",
                headers={
                    "Cache-Control": "no-cache",
                    "X-Accel-Buffering": "no",
                    "X-Session-Id": session_id,
                    "X-Run-Id": existing_run_id,
                },
            )
    transcript, run_ids = _truncate_for_retry(
        transcript=transcript,
        run_ids=run_ids,
        retry_from_run_id=body.retry_from_run_id,
    )
    client_requests = _filter_client_requests_for_runs(client_requests, run_ids)

    transcript.append(("user", body.message))
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_ids.append(run_id)
    if request_key:
        client_requests[request_key] = {
            "run_id": run_id,
            "transcript_user_index": len(transcript) - 1,
        }
    bundle.session_store.upsert(
        session_id=session_id,
        thread_id=thread_id,
        run_ids=run_ids,
        transcript=transcript,
        client_requests=client_requests,
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
        tool_policy=_chat_tool_policy(body=body, settings=settings),
        app_metadata=build_chat_app_metadata(settings, session_id),
    )

    def _persist_assistant(assistant_text: str, _terminal_event: str | None) -> None:
        next_transcript = list(transcript)
        if _terminal_event == "run_completed" and assistant_text.strip():
            next_transcript.append(("assistant", assistant_text))
        run_metadata = aggregate_metadata_from_events(
            replay_events_for_run(bundle, run_id),
        )
        metadata_patch = {run_id: run_metadata} if run_metadata else None
        bundle.session_store.upsert(
            session_id=session_id,
            thread_id=thread_id,
            run_ids=run_ids,
            transcript=next_transcript,
            metadata_by_run=metadata_patch,
            client_requests=client_requests,
        )

    ensure_run_task(
        agent=bundle.agent,
        run_input=run_input,
        event_log=bundle.event_log,
        on_finish=_persist_assistant,
    )
    stream = _tail_existing_run(
        bundle=bundle,
        run_id=run_id,
        request=request,
        last_event_id=request.headers.get("Last-Event-ID"),
        timeout_seconds=settings.deadline_seconds,
        keepalive_seconds=settings.sse_keepalive_seconds,
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


@router.post("/chat/runs/{run_id}/cancel", response_model=CancelRunResponse)
def cancel_run(run_id: str) -> CancelRunResponse:
    """Request cooperative cancellation for an in-flight run."""
    request_cancel(run_id)
    return CancelRunResponse(run_id=run_id, cancelled=is_cancelled(run_id))


@router.post("/chat/runs/{run_id}/control", response_model=ChatControlResponse)
def control_run(
    run_id: str,
    body: ChatControlRequest,
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> ChatControlResponse:
    """Queue a typed steering command for the next runtime boundary."""
    response = bundle.agent.control(
        ControlRequest(
            kind=body.kind,
            run_id=run_id,
            thread_id=body.thread_id,
            agent_id=body.agent_id,
            priority=body.priority,
            payload=body.payload,
            source="chat-demo",
            dedupe_key=body.dedupe_key,
        )
    )
    _persist_steering_history(
        bundle=bundle,
        run_id=run_id,
        queue_id=response.queue_id,
        control_id=response.control_id,
        status="queued" if response.ok else "failed",
    )
    return ChatControlResponse(
        ok=response.ok,
        control_id=response.control_id,
        queue_id=response.queue_id,
        error=response.error,
    )


@router.delete("/chat/commands/{queue_id}", response_model=ChatControlResponse)
def cancel_queued_command(
    queue_id: str,
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> ChatControlResponse:
    """Cancel a queued steering command before it is applied."""
    queued = bundle.command_queue_store.get(queue_id)
    response = bundle.agent.cancel_queued_message(queue_id)
    _persist_steering_history(
        bundle=bundle,
        run_id=queued.run_id if queued is not None else None,
        queue_id=queue_id,
        control_id=response.control_id,
        status="cancelled" if response.ok else "failed",
    )
    return ChatControlResponse(
        ok=response.ok,
        control_id=response.control_id,
        queue_id=response.queue_id,
        error=response.error,
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
            "app_metadata": merge_resume_app_metadata(
                settings,
                base_metadata=(
                    dict(base_input.app_metadata)
                    if isinstance(base_input.app_metadata, dict)
                    else None
                ),
                run_id=run_id,
                session_store=bundle.session_store,
            ),
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
