"""Streaming chat and resume endpoints."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import AsyncIterator
from urllib.parse import urlparse

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
    DeepResearchArtifactState,
    DeepResearchArtifactsState,
    DeepResearchMetricsState,
    DeepResearchSourceCounts,
    DeepResearchSubagentState,
    DeepResearchTodoState,
    DeepResearchTraceState,
    DeepResearchViewState,
    HardResearchOptions,
    InterruptView,
    ResumeRequest,
)
from app.services.agent_factory import AgentBundle
from app.services.message_metadata import aggregate_metadata_from_events
from app.services.run_trace_summary import summarize_run_trace
from app.sse_relay import ensure_run_task, relay_and_capture
from app.workspace import build_chat_app_metadata, merge_resume_app_metadata
from app.workspace import list_workspace_artifacts
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse

from agent_driver.adapters import parse_after_seq, render_sse_line
from agent_driver.context import (
    filter_client_requests_for_runs,
    record_mapping_dict,
    transcript_to_messages,
    truncate_transcript_for_retry,
    turn_text_for_run,
)
from agent_driver.contracts import (
    AgentRunInput,
    ControlRequest,
    ToolPolicyInput,
)
from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.interrupts import InterruptRequest, ResumeCommand
from agent_driver.runtime.chat_policy import (
    build_chat_tool_policy,
    initial_tool_choice_for_chat,
)
from agent_driver.runtime.deep_research_phase_gate import (
    create_deep_research_phase_gate,
)
from agent_driver.runtime.stream import backfill_stream_events, project_runtime_events
from agent_driver.runtime.task_contract import build_chat_task_contract
from agent_driver.runtime.tool_gate import ToolGate

router = APIRouter(tags=["chat"])
_TERMINAL_EVENTS = {
    "interrupt_requested",
    "run_completed",
    "run_failed",
    "run_cancelled",
}


def _client_requests_dict(record: object | None) -> dict[str, dict[str, object]]:
    return record_mapping_dict(record, "client_requests")


def _metadata_by_run_dict(record: object | None) -> dict[str, dict[str, object]]:
    return record_mapping_dict(record, "metadata_by_run")


def _session_record_for_run(bundle: AgentBundle, run_id: str):
    for record in bundle.session_store.list_sessions():
        if run_id in record.run_ids:
            return record
    return None


def _run_failed_message(events: list[dict[str, object]]) -> str:
    for event in reversed(events):
        if event.get("event") != "run_failed":
            continue
        data = event.get("data")
        if not isinstance(data, dict):
            break
        status_code = data.get("status_code")
        message = data.get("message")
        reason = data.get("reason")
        detail = message if isinstance(message, str) and message.strip() else reason
        if status_code == 402:
            suffix = (
                detail
                if isinstance(detail, str) and detail.strip()
                else "Check OpenRouter credits, model availability, or choose another model."
            )
            return f"**Run failed**\n\nProvider rejected the request with HTTP 402. {suffix}"
        if isinstance(status_code, int):
            suffix = f" {detail}" if isinstance(detail, str) and detail.strip() else ""
            return f"**Run failed**\n\nProvider rejected the request with HTTP {status_code}.{suffix}"
        if isinstance(detail, str) and detail.strip():
            return f"**Run failed**\n\n{detail.strip()}"
        break
    return "**Run failed**\n\nThe model provider rejected or interrupted the request."


def _turn_text_for_run(
    record: object | None,
    run_id: str,
) -> tuple[str | None, str | None]:
    if record is None:
        return None, None
    return turn_text_for_run(
        transcript=getattr(record, "transcript", ()),
        run_ids=getattr(record, "run_ids", ()),
        run_id=run_id,
    )


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
        controls = [dict(item) for item in existing_controls if isinstance(item, dict)]
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


def _effective_research_mode(body: ChatMessageRequest) -> str:
    if body.research_mode in {"chat", "web", "deep"}:
        return body.research_mode
    if body.research_depth == "deep_parallel_research":
        return "deep"
    preset = resolve_tool_preset(body.tool_preset)
    if preset in {"web", "web_search", "web_fetch"}:
        return "web"
    return "chat"


def _effective_research_profile(body: ChatMessageRequest) -> str:
    mode = _effective_research_mode(body)
    if mode == "deep":
        if body.research_profile in {"medium", "hard"}:
            return body.research_profile
        return "medium"
    if mode == "web":
        return "light"
    return "light"


def _effective_profile_source(body: ChatMessageRequest) -> str:
    if body.profile_source in {
        "user_selected",
        "auto_suggested",
        "backend_classified",
        "scenario_forced",
    }:
        return body.profile_source
    if body.research_mode is None and body.research_depth == "deep_parallel_research":
        return "backend_classified"
    return "user_selected"


def _effective_hard_options(body: ChatMessageRequest) -> dict[str, bool]:
    options = body.hard_options or HardResearchOptions()
    payload = options.model_dump(mode="json")
    if _effective_research_profile(body) != "hard":
        payload["allow_browser_action"] = False
    return {
        "allow_pdf_read": bool(payload.get("allow_pdf_read")),
        "allow_browser_read": bool(payload.get("allow_browser_read")),
        "allow_browser_action": bool(payload.get("allow_browser_action")),
    }


def _research_request_metadata(body: ChatMessageRequest) -> dict[str, object]:
    mode = _effective_research_mode(body)
    profile = _effective_research_profile(body)
    return {
        "research_mode": mode,
        "research_profile": profile,
        "profile_source": _effective_profile_source(body),
        "hard_options": _effective_hard_options(body),
        "research_depth": (
            "deep_parallel_research" if mode == "deep" else body.research_depth
        ),
    }


def _effective_chat_preset(body: ChatMessageRequest) -> ToolPreset:
    """Return runtime tool preset, upgrading Deep Research to artifact tools."""
    if body.research_mode == "deep" or body.research_depth == "deep_parallel_research":
        if body.research_profile == "hard":
            return "deep_research_hard"
        return "deep_research_medium"
    if body.research_mode == "web":
        return "research_light"
    if body.research_mode == "chat":
        return "off"
    return resolve_tool_preset(body.tool_preset)


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


def _chat_tool_policy(
    *, body: ChatMessageRequest, settings: Settings
) -> ToolPolicyInput:
    force_planning = (
        body.force_planning
        if body.force_planning is not None
        else settings.force_planning
    )
    policy = build_chat_tool_policy(
        body.message,
        force_planning=force_planning,
        force_planning_mode=settings.force_planning_mode,
    )
    if _effective_research_mode(body) != "deep":
        return policy
    metadata = dict(policy.metadata)
    task_contract = dict(metadata.get("task_contract") or {})
    request_metadata = _research_request_metadata(body)
    task_contract.update(
        {
            "kind": "research",
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": request_metadata["research_profile"],
            "profile_source": request_metadata["profile_source"],
            "hard_options": request_metadata["hard_options"],
            "max_subagent_requests": _deep_research_max_subagent_requests(
                request_metadata["research_profile"]
            ),
            "goal": task_contract.get("goal") or body.message,
            "approach": (
                "Use the shared Deep Research runtime contract: discover "
                "candidate sources, verify concrete reads, preserve the source "
                "ledger, and keep final synthesis in the parent run."
            ),
        }
    )
    metadata["task_contract"] = task_contract
    metadata["deep_research_mode"] = {
        "enabled": True,
        "research_depth": "deep_parallel_research",
        "research_mode": "deep",
        "research_profile": request_metadata["research_profile"],
        "profile_source": request_metadata["profile_source"],
        "hard_options": request_metadata["hard_options"],
    }
    if settings.deep_research_phase_gate_enabled:
        metadata["deep_research_phase_gate"] = {
            "enabled": True,
            "required_fetch_attempts": (
                4 if request_metadata["research_profile"] == "hard" else 2
            ),
        }
    return policy.model_copy(update={"metadata": metadata})


def _deep_research_max_subagent_requests(profile: str) -> int:
    if profile == "light":
        return 0
    if profile == "hard":
        return 4
    return 1


def _chat_tool_gate(*, body: ChatMessageRequest, settings: Settings) -> ToolGate | None:
    if _effective_research_mode(body) != "deep":
        return None
    if not settings.deep_research_phase_gate_enabled:
        return None
    required_fetch_attempts = (
        4 if _effective_research_profile(body) == "hard" else 2
    )
    return create_deep_research_phase_gate(
        required_fetch_attempts=required_fetch_attempts
    )


def _trace_summary_for_run(
    *,
    bundle: AgentBundle,
    run_id: str,
    events: list[dict[str, object]],
) -> dict[str, object]:
    record = _session_record_for_run(bundle, run_id)
    user_prompt, assistant_text = _turn_text_for_run(record, run_id)
    task_contract = (
        build_chat_task_contract(user_prompt)
        if isinstance(user_prompt, str) and user_prompt.strip()
        else None
    )
    return summarize_run_trace(
        run_id=run_id,
        events=events,
        user_prompt=user_prompt,
        assistant_text=assistant_text,
        task_contract=task_contract,
    )


def _deep_research_artifact_state(
    *,
    path: str,
    artifacts_by_path: dict[str, object],
    lifecycle: str,
) -> DeepResearchArtifactState | None:
    item = artifacts_by_path.get(path)
    if item is None:
        return None
    return DeepResearchArtifactState(
        path=getattr(item, "path"),
        kind=getattr(item, "kind"),
        sizeBytes=getattr(item, "size_bytes"),
        modifiedAt=getattr(item, "modified_at"),
        lifecycle=lifecycle,
        previewAvailable=True,
    )


def _deep_research_artifacts_state(
    *,
    settings: Settings,
    session_id: str | None,
    trace_summary: dict[str, object],
) -> DeepResearchArtifactsState:
    if not session_id:
        return DeepResearchArtifactsState()
    artifacts = list_workspace_artifacts(settings, session_id)
    by_path = {item.path: item for item in artifacts}
    trace_artifacts = trace_summary.get("artifacts")
    lifecycle = "created"
    if isinstance(trace_artifacts, dict):
        if int(trace_artifacts.get("report_patch_count") or 0) > 0:
            lifecycle = "patched"
        elif int(trace_artifacts.get("report_targeted_edit_count") or 0) > 0:
            lifecycle = "edited"
    return DeepResearchArtifactsState(
        report=_deep_research_artifact_state(
            path="research/report.md",
            artifacts_by_path=by_path,
            lifecycle=lifecycle,
        ),
        sourceLedger=_deep_research_artifact_state(
            path="research/sources.jsonl",
            artifacts_by_path=by_path,
            lifecycle="updated",
        ),
        claims=(
            _deep_research_artifact_state(
                path="research/claims.jsonl",
                artifacts_by_path=by_path,
                lifecycle="updated",
            )
            or _deep_research_artifact_state(
                path="research/claims.md",
                artifacts_by_path=by_path,
                lifecycle="updated",
            )
        ),
    )


def _source_domains_from_metadata(metadata: dict[str, object]) -> set[str]:
    raw_sources = metadata.get("source_evidence") or metadata.get("sourceEvidence")
    if not isinstance(raw_sources, list):
        return set()
    domains: set[str] = set()
    for item in raw_sources:
        if not isinstance(item, dict):
            continue
        url = item.get("url") or item.get("final_url") or item.get("finalUrl")
        if not isinstance(url, str) or not url:
            continue
        domain = urlparse(url).netloc.lower()
        if domain:
            domains.add(domain)
    return domains


def _deep_research_source_counts(
    *,
    metadata: dict[str, object],
    trace_summary: dict[str, object],
) -> DeepResearchSourceCounts:
    efficiency = trace_summary.get("research_efficiency")
    if not isinstance(efficiency, dict):
        efficiency = {}
    research = trace_summary.get("research")
    if not isinstance(research, dict):
        research = {}
    return DeepResearchSourceCounts(
        verified=int(efficiency.get("verified_read_count") or 0),
        candidates=int(efficiency.get("candidate_count") or 0),
        blocked=int(efficiency.get("blocked_read_count") or 0),
        failed=int(efficiency.get("failed_read_count") or 0),
        distinctDomains=max(
            len(_source_domains_from_metadata(metadata)),
            _domain_count(
                research.get("unique_domains") or research.get("distinct_domains")
            ),
        ),
    )


def _domain_count(value: object) -> int:
    if isinstance(value, list):
        return len([item for item in value if isinstance(item, str) and item.strip()])
    if isinstance(value, int) and not isinstance(value, bool):
        return max(0, value)
    if isinstance(value, str):
        try:
            return max(0, int(value))
        except ValueError:
            return 0
    return 0


def _deep_research_todos(trace_summary: dict[str, object]) -> DeepResearchTodoState:
    planning = trace_summary.get("planning")
    if not isinstance(planning, dict):
        return DeepResearchTodoState()
    done = int(planning.get("done_count") or planning.get("completed_count") or 0)
    total = int(planning.get("total_count") or planning.get("todo_count") or 0)
    current = planning.get("current")
    if not isinstance(current, str):
        current = planning.get("current_title")
    return DeepResearchTodoState(
        done=done,
        total=total,
        current=current if isinstance(current, str) and current else None,
        stale=bool(planning.get("todos_incomplete") or False),
    )


def _deep_research_metrics(trace_summary: dict[str, object]) -> DeepResearchMetricsState:
    efficiency = trace_summary.get("research_efficiency")
    if not isinstance(efficiency, dict):
        efficiency = {}
    llm = trace_summary.get("llm")
    usage = llm.get("usage") if isinstance(llm, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    tool_names = trace_summary.get("tool_names")
    names = [str(name) for name in tool_names] if isinstance(tool_names, list) else []
    return DeepResearchMetricsState(
        promptTokens=_int_or_none(usage.get("input_tokens") or usage.get("prompt_tokens")),
        completionTokens=_int_or_none(
            usage.get("output_tokens") or usage.get("completion_tokens")
        ),
        totalTokens=_int_or_none(usage.get("total_tokens")),
        webSearchCount=names.count("web_search"),
        webFetchCount=names.count("web_fetch"),
        reportFullWriteCount=int(efficiency.get("report_full_write_count") or 0),
        reportPatchCount=int(efficiency.get("report_patch_count") or 0),
        longChatBeforeReportChars=int(
            efficiency.get("long_chat_before_report_chars") or 0
        ),
    )


def _deep_research_subagents(trace_summary: dict[str, object]) -> DeepResearchSubagentState:
    subagents = trace_summary.get("subagents")
    if not isinstance(subagents, dict):
        return DeepResearchSubagentState()
    failed = int(subagents.get("child_error_count") or 0)
    completed = int(subagents.get("runs_completed") or 0)
    total = max(completed + failed, int(subagents.get("runs_started") or 0))
    running = max(0, total - completed - failed)
    return DeepResearchSubagentState(
        totalChildren=total,
        runningChildren=running,
        completedChildren=completed,
        failedChildren=failed,
        duplicatedQueries=0,
    )


def _deep_research_phase(trace_summary: dict[str, object]) -> str:
    efficiency = trace_summary.get("research_efficiency")
    if isinstance(efficiency, dict):
        phase = efficiency.get("deep_research_phase")
        if isinstance(phase, str) and phase:
            return phase
    terminal = trace_summary.get("terminal_event")
    if terminal == "run_completed":
        return "ready"
    if terminal in {"run_failed", "run_cancelled"}:
        return "failed" if terminal == "run_failed" else "cancelled"
    return "starting"


def _deep_research_readiness(trace_summary: dict[str, object]) -> str:
    final_readiness = trace_summary.get("final_readiness")
    if isinstance(final_readiness, str) and final_readiness:
        if final_readiness == "allowed":
            return "ready"
        return final_readiness
    if trace_summary.get("verdict") == "pass":
        return "ready"
    failures = trace_summary.get("failures")
    if isinstance(failures, dict):
        if failures.get("deep_research_no_report_artifact"):
            return "needs_report"
        if failures.get("deep_research_no_source_ledger_artifact"):
            return "needs_more_sources"
    return "needs_review"


def _deep_research_warnings(trace_summary: dict[str, object]) -> list[str]:
    failures = trace_summary.get("failures")
    if not isinstance(failures, dict):
        return []
    return sorted(str(key) for key, value in failures.items() if value)


def _int_or_none(value: object) -> int | None:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, float):
        return int(value)
    return None


@router.post("/chat/messages")
async def chat_messages(
    body: ChatMessageRequest,
    request: Request,
    settings: Settings = Depends(get_settings),
) -> StreamingResponse:
    """Start one run and stream normalized runtime events as SSE."""
    preset = _effective_chat_preset(body)
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
    transcript, run_ids = truncate_transcript_for_retry(
        transcript=transcript,
        run_ids=run_ids,
        retry_from_run_id=body.retry_from_run_id,
    )
    client_requests = filter_client_requests_for_runs(client_requests, run_ids)

    transcript.append(("user", body.message))
    run_id = f"run_{uuid.uuid4().hex[:12]}"
    run_ids.append(run_id)
    request_run_metadata = _research_request_metadata(body)
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
        metadata_by_run={run_id: request_run_metadata},
        client_requests=client_requests,
    )
    tool_policy = _chat_tool_policy(body=body, settings=settings)
    tool_gate = _chat_tool_gate(body=body, settings=settings)
    run_input = AgentRunInput(
        input=body.message,
        messages=transcript_to_messages(transcript),
        run_id=run_id,
        thread_id=thread_id,
        agent_id="chat-demo-agent",
        graph_preset="single_react",
        stream=True,
        max_steps=settings.max_steps,
        max_tool_calls=settings.max_tool_calls,
        deadline_seconds=settings.deadline_seconds,
        tool_policy=tool_policy,
        tool_choice=initial_tool_choice_for_chat(policy=tool_policy, preset=preset),
        app_metadata=build_chat_app_metadata(
            settings,
            session_id,
            scenario_id=body.scenario_id,
            research_metadata=request_run_metadata,
        ),
    )

    def _persist_assistant(assistant_text: str, _terminal_event: str | None) -> None:
        next_transcript = list(transcript)
        events = replay_events_for_run(bundle, run_id)
        if _terminal_event == "run_completed" and assistant_text.strip():
            next_transcript.append(("assistant", assistant_text))
        elif _terminal_event == "run_failed":
            next_transcript.append(("assistant", _run_failed_message(events)))
        run_metadata = {
            **request_run_metadata,
            **aggregate_metadata_from_events(events),
        }
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
        tool_gate=tool_gate,
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


@router.get("/chat/runs/{run_id}/trace-summary")
def get_run_trace_summary(
    run_id: str,
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> dict[str, object]:
    """Return compact scenario diagnostics for one run."""
    events = replay_events_for_run(bundle, run_id)
    if not events:
        raise HTTPException(status_code=404, detail="run not found")
    return _trace_summary_for_run(
        bundle=bundle,
        run_id=run_id,
        events=events,
    )


@router.get(
    "/chat/runs/{run_id}/deep-research-state",
    response_model=DeepResearchViewState,
)
def get_deep_research_state(
    run_id: str,
    settings: Settings = Depends(get_settings),
    bundle: AgentBundle = Depends(get_agent_bundle),
) -> DeepResearchViewState:
    """Return canonical run-level Deep Research UI projection."""
    events = replay_events_for_run(bundle, run_id)
    if not events:
        raise HTTPException(status_code=404, detail="run not found")
    record = _session_record_for_run(bundle, run_id)
    metadata_by_run = _metadata_by_run_dict(record)
    metadata = dict(metadata_by_run.get(run_id, {}))
    trace_summary = _trace_summary_for_run(
        bundle=bundle,
        run_id=run_id,
        events=events,
    )
    failures = trace_summary.get("failures")
    failure_flags = (
        sorted(str(key) for key, value in failures.items() if value)
        if isinstance(failures, dict)
        else []
    )
    return DeepResearchViewState(
        runId=run_id,
        sessionId=getattr(record, "session_id", None),
        researchMode=str(metadata.get("research_mode") or "unknown"),
        profile=str(metadata.get("research_profile") or "unknown"),
        profileSource=str(metadata.get("profile_source") or "unknown"),
        phase=_deep_research_phase(trace_summary),
        readiness=_deep_research_readiness(trace_summary),
        todos=_deep_research_todos(trace_summary),
        artifacts=_deep_research_artifacts_state(
            settings=settings,
            session_id=getattr(record, "session_id", None),
            trace_summary=trace_summary,
        ),
        sources=_deep_research_source_counts(
            metadata=metadata,
            trace_summary=trace_summary,
        ),
        subagents=_deep_research_subagents(trace_summary),
        metrics=_deep_research_metrics(trace_summary),
        warnings=_deep_research_warnings(trace_summary),
        trace=DeepResearchTraceState(
            runId=run_id,
            verdict=trace_summary.get("verdict")
            if isinstance(trace_summary.get("verdict"), str)
            else None,
            terminalEvent=trace_summary.get("terminal_event")
            if isinstance(trace_summary.get("terminal_event"), str)
            else None,
            failureFlags=failure_flags,
        ),
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
