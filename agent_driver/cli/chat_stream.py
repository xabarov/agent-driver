"""Streaming render helpers for chat CLI turns."""

from __future__ import annotations

import json
import time
from collections.abc import Callable
from dataclasses import dataclass

from agent_driver.contracts import RunStreamEvent
from agent_driver.cli.tui.renderer import ChatRenderer, build_renderer
from agent_driver.cli.tui.spinner import StatusSpinner
from agent_driver.cli.tui.streaming import MarkdownStreamBuffer

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}
_TOKEN_PRESSURE_LABELS = {
    "ok": "ok",
    "warning": "warn",
    "compact_recommended": "compact",
    "blocking": "blocking",
}


@dataclass(slots=True)
class ToolCardEvent:
    """Renderable information for one tool event."""

    name: str
    args_summary: str
    status: str | None
    result_summary: str | None
    truncated: bool | None = None
    error_code: str | None = None


@dataclass(slots=True)
class ToolState:
    """In-flight tool call state."""

    name: str
    args_summary: str
    status: str | None = None
    result_summary: str | None = None
    truncated: bool | None = None
    error_code: str | None = None


def _format_compact_event(event: RunStreamEvent) -> str | None:
    name = event.event
    if name in {
        "run_started",
        "llm_call_started",
        "llm_call_completed",
        "checkpoint_saved",
        "node_started",
        "node_completed",
        "guardrail_decision",
    }:
        return None
    if name == "run_completed":
        return None
    if name in {"run_failed", "run_cancelled"}:
        reason = event.data.get("reason")
        return f"run {name}" if reason is None else f"run {name} reason={reason}"
    if name in {"tool_call_started", "tool_call_completed"}:
        return None
    if name == "warning":
        kind = str(event.data.get("kind", "warning"))
        if kind == "tool_protocol_debug":
            return (
                "warning kind=tool_protocol_debug "
                f"messages={event.data.get('message_count')} "
                f"roles={event.data.get('roles')} "
                f"tool_choice={event.data.get('tool_choice')} "
                f"tool_names={event.data.get('tool_names')}"
            )
        return f"warning kind={kind}"
    if name in {"interrupt_requested", "run_paused"}:
        reason = event.data.get("reason", "unknown")
        return f"interrupt reason={reason}"
    return f"event {name}"


_FULL_ARG_TOOLS = {"glob_search", "grep_search", "web_search", "read_file"}
_PRIORITY_ARG_KEYS = ("pattern", "base_dir", "max_results", "path", "query", "path_glob")


def _truncate_value(value: object, *, limit: int = 40) -> str:
    text = json.dumps(value, ensure_ascii=False) if isinstance(value, (dict, list)) else str(value)
    text = text.replace("\n", " ")
    if len(text) <= limit:
        return text
    return f"{text[:limit].rstrip()}..."


def _summarize_tool_args(args: object, *, tool_name: str | None = None) -> str:
    if not isinstance(args, dict) or not args:
        return ""
    if tool_name in _FULL_ARG_TOOLS:
        parts: list[str] = []
        seen: set[str] = set()
        for key in _PRIORITY_ARG_KEYS:
            if key in args:
                parts.append(f"{key}={_truncate_value(args[key], limit=120)}")
                seen.add(key)
        for key, value in args.items():
            if key in seen:
                continue
            parts.append(f"{key}={_truncate_value(value, limit=60)}")
        return ", ".join(parts)
    parts: list[str] = []
    for idx, (key, value) in enumerate(args.items()):
        if idx >= 3:
            parts.append("...")
            break
        parts.append(f"{key}={_truncate_value(value)}")
    return ", ".join(parts)


def _tool_state_key(tool: dict[str, object], *, fallback_index: int = 0) -> str:
    tool_call_id = tool.get("tool_call_id")
    if isinstance(tool_call_id, str) and tool_call_id:
        return f"id:{tool_call_id}"
    name = str(tool.get("tool_name") or "?")
    return f"name:{name}:{fallback_index}"


def _extract_tool_states(event: RunStreamEvent) -> dict[str, ToolState]:
    states: dict[str, ToolState] = {}
    if event.event not in {"tool_call_started", "tool_call_completed"}:
        return states
    tools = event.data.get("tools")
    if isinstance(tools, list) and tools:
        for idx, tool in enumerate(tools):
            if not isinstance(tool, dict):
                continue
            key = _tool_state_key(tool, fallback_index=idx)
            states[key] = ToolState(
                name=str(tool.get("tool_name") or "?"),
                args_summary=_summarize_tool_args(
                    tool.get("args"), tool_name=str(tool.get("tool_name") or "")
                ),
                status=str(tool.get("status")) if tool.get("status") is not None else None,
                result_summary=_merge_result_preview_paths(
                    tool_name=str(tool.get("tool_name") or ""),
                    result_summary=str(tool.get("result_summary")).strip()
                    if isinstance(tool.get("result_summary"), str)
                    else None,
                    preview_paths=tool.get("result_preview_paths"),
                ),
                truncated=(
                    bool(tool.get("truncated"))
                    if isinstance(tool.get("truncated"), bool)
                    else None
                ),
                error_code=(
                    str(tool.get("error_code"))
                    if isinstance(tool.get("error_code"), str)
                    else None
                ),
            )
        return states
    data_tool: dict[str, object] = {
        "tool_name": event.data.get("tool_name"),
        "args": event.data.get("args"),
        "status": event.data.get("status"),
        "result_summary": event.data.get("result_summary"),
        "truncated": event.data.get("truncated"),
        "error_code": event.data.get("error_code"),
        "tool_call_id": event.data.get("tool_call_id"),
        "result_preview_paths": event.data.get("result_preview_paths"),
    }
    states[_tool_state_key(data_tool)] = ToolState(
        name=str(event.data.get("tool_name") or "?"),
        args_summary=_summarize_tool_args(
            event.data.get("args"), tool_name=str(event.data.get("tool_name") or "")
        ),
        status=str(event.data.get("status")) if event.data.get("status") is not None else None,
        result_summary=_merge_result_preview_paths(
            tool_name=str(event.data.get("tool_name") or ""),
            result_summary=str(event.data.get("result_summary")).strip()
            if isinstance(event.data.get("result_summary"), str)
            else None,
            preview_paths=event.data.get("result_preview_paths"),
        ),
        truncated=(
            bool(event.data.get("truncated"))
            if isinstance(event.data.get("truncated"), bool)
            else None
        ),
        error_code=(
            str(event.data.get("error_code"))
            if isinstance(event.data.get("error_code"), str)
            else None
        ),
    )
    return states


def _merge_result_preview_paths(
    *, tool_name: str, result_summary: str | None, preview_paths: object
) -> str | None:
    if tool_name not in {"glob_search", "web_search"}:
        return result_summary
    if not isinstance(preview_paths, list) or not preview_paths:
        return result_summary
    normalized = [str(item) for item in preview_paths if isinstance(item, str)]
    if not normalized:
        return result_summary
    preview_text = ", ".join(normalized[:5])
    sample_label = "sample" if tool_name == "glob_search" else "sample_urls"
    if result_summary:
        return f"{result_summary}; {sample_label}={preview_text}"
    return f"{sample_label}={preview_text}"


def _extract_planned_tool_args(event: RunStreamEvent) -> dict[str, str]:
    if event.event != "llm_call_completed":
        return {}
    payload = event.data
    planned = payload.get("planned_tool_calls")
    if not isinstance(planned, list):
        metadata = payload.get("metadata")
        if isinstance(metadata, dict):
            planned = metadata.get("planned_tool_calls")
    if not isinstance(planned, list):
        return {}
    result: dict[str, str] = {}
    for idx, item in enumerate(planned):
        if not isinstance(item, dict):
            continue
        tool_call_id = item.get("tool_call_id")
        name = str(item.get("tool_name") or "?")
        args_summary = _summarize_tool_args(
            item.get("args"), tool_name=str(item.get("tool_name") or "")
        )
        if isinstance(tool_call_id, str) and tool_call_id:
            result[f"id:{tool_call_id}"] = args_summary
        result[f"name:{name}:{idx}"] = args_summary
    return result


def _extract_usage_totals(event: RunStreamEvent) -> tuple[int | None, int | None]:
    def _scan_usage(value: object) -> tuple[int | None, int | None]:
        if isinstance(value, dict):
            candidate = value.get("usage")
            if isinstance(candidate, dict):
                in_candidate = candidate.get("input_tokens", candidate.get("prompt_tokens"))
                out_candidate = candidate.get("output_tokens", candidate.get("completion_tokens"))
                in_total = int(in_candidate) if isinstance(in_candidate, int) else None
                out_total = int(out_candidate) if isinstance(out_candidate, int) else None
                if in_total is not None or out_total is not None:
                    return in_total, out_total
            for nested in value.values():
                in_total, out_total = _scan_usage(nested)
                if in_total is not None or out_total is not None:
                    return in_total, out_total
        if isinstance(value, list):
            for nested in value:
                in_total, out_total = _scan_usage(nested)
                if in_total is not None or out_total is not None:
                    return in_total, out_total
        return None, None

    data = event.data
    input_tokens = data.get("input_tokens")
    output_tokens = data.get("output_tokens")
    if isinstance(data.get("usage"), dict):
        usage = data["usage"]
        input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", input_tokens))
        output_tokens = usage.get("output_tokens", usage.get("completion_tokens", output_tokens))
    nested_input, nested_output = _scan_usage(data)
    if nested_input is not None:
        input_tokens = nested_input
    if nested_output is not None:
        output_tokens = nested_output
    in_total = int(input_tokens) if isinstance(input_tokens, int) else None
    out_total = int(output_tokens) if isinstance(output_tokens, int) else None
    return in_total, out_total


async def render_chat_stream(
    *,
    stream,
    output: Callable[[str], None],
    run_id: str,
    renderer: ChatRenderer | None = None,
    animate: bool = False,
) -> tuple[str, int, int, str | None]:
    """Render stream to chat-oriented output and return assistant text."""
    active_renderer = renderer or build_renderer(
        output=output, ui_mode="rich" if animate else "plain"
    )
    assistant_parts: list[str] = []
    stream_buffer = MarkdownStreamBuffer()
    token_line_open = False
    saw_terminal = False
    tools_used = 0
    warnings_seen = 0
    input_tokens = 0
    output_tokens = 0
    saw_usage = False
    terminal_error = False
    latest_pressure: str | None = None
    started_at = time.monotonic()
    spinner = StatusSpinner(
        output=output,
        enabled=animate and active_renderer.rich_enabled,
        console=active_renderer.live_console,
    )
    spinner.start()
    pending_tools: dict[str, ToolState] = {}
    planned_args: dict[str, str] = {}
    saw_denied_tool = False
    try:
        async for event in stream:
            for key, args_summary in _extract_planned_tool_args(event).items():
                planned_args[key] = args_summary
            if event.event == "token_delta":
                if not token_line_open:
                    await spinner.stop()
                    active_renderer.emit_raw(active_renderer.assistant_prefix())
                    token_line_open = True
                delta = str(event.data.get("delta_text") or "")
                if delta:
                    assistant_parts.append(delta)
                    spinner.increment_tokens(delta)
                    if active_renderer.rich_enabled:
                        stable_chunk, _ = stream_buffer.append(delta)
                        if stable_chunk:
                            active_renderer.emit_assistant_tail(stable_chunk)
                    else:
                        active_renderer.emit_assistant_delta(delta)
                    output_tokens += len(delta)
                continue
            usage_in, usage_out = _extract_usage_totals(event)
            if usage_in is not None:
                input_tokens = usage_in
                saw_usage = True
            if usage_out is not None:
                output_tokens = usage_out
                saw_usage = True
            tool_states = _extract_tool_states(event)
            cards_to_emit: list[ToolCardEvent] = []
            if event.event == "tool_call_started":
                for key, state in tool_states.items():
                    if not state.args_summary:
                        state.args_summary = planned_args.get(key, "")
                    pending_tools[key] = state
                    label_args = f"({state.args_summary})" if state.args_summary else "()"
                    spinner.set_label(f"Calling {state.name}{label_args}...")
            elif event.event == "tool_call_completed":
                for key, state in tool_states.items():
                    previous = pending_tools.pop(key, None)
                    if previous is not None:
                        if not state.args_summary:
                            state.args_summary = previous.args_summary
                        if state.result_summary is None:
                            state.result_summary = previous.result_summary
                        if state.status is None:
                            state.status = previous.status
                    if not state.args_summary:
                        state.args_summary = planned_args.get(key, "")
                    cards_to_emit.append(
                        ToolCardEvent(
                            name=state.name,
                            args_summary=state.args_summary,
                            status=state.status,
                            result_summary=state.result_summary,
                            truncated=state.truncated,
                            error_code=state.error_code,
                        )
                    )
            if cards_to_emit:
                await spinner.stop()
            compact = _format_compact_event(event)
            if compact is None and not cards_to_emit and event.event != "run_failed":
                continue
            if token_line_open:
                if active_renderer.rich_enabled:
                    tail = stream_buffer.finalize()
                    if tail:
                        active_renderer.emit_assistant_tail(tail)
                active_renderer.emit_raw("\n")
                token_line_open = False
            for card in cards_to_emit:
                tools_used += 1
                if card.status in {"denied", "error"}:
                    saw_denied_tool = True
                active_renderer.emit_tool_card(
                    name=card.name,
                    args_summary=card.args_summary,
                    status=card.status,
                    result_summary=card.result_summary,
                    truncated=card.truncated,
                    error_code=card.error_code,
                )
            if compact is None:
                compact = ""
            if compact.startswith("warning "):
                warnings_seen += 1
                if event.data.get("kind") == "token_pressure":
                    state_raw = str(event.data.get("state", "ok"))
                    latest_pressure = _TOKEN_PRESSURE_LABELS.get(state_raw, state_raw)
                active_renderer.emit_warning(compact)
            elif compact:
                active_renderer.emit_event(compact)
            if event.event == "run_failed":
                terminal_error = True
                reason = str(event.data.get("reason") or "run_failed")
                if reason == "tool_policy_denied" and saw_denied_tool:
                    continue
                hint = (
                    "Increase --max-steps if needed."
                    if reason == "max_steps_exceeded"
                    else "Provider rejected the request payload; retry with fewer tool calls."
                    if reason == "provider_protocol"
                    else "Check --max-tool-calls and tool policy."
                    if reason == "tool_policy_denied"
                    else None
                )
                active_renderer.emit_error_card(
                    title="Run failed",
                    reason=reason,
                    hint=hint,
                )
            if event.event in _TERMINAL_EVENTS:
                saw_terminal = True
            elif not token_line_open:
                spinner.set_label("Pondering...")
                spinner.start()
    finally:
        await spinner.stop()
    if token_line_open:
        if active_renderer.rich_enabled:
            tail = stream_buffer.finalize()
            if tail:
                active_renderer.emit_assistant_tail(tail)
        active_renderer.emit_raw("\n")
    assistant_text = "".join(assistant_parts)
    if not assistant_text and not saw_terminal:
        active_renderer.emit_raw(f"{active_renderer.assistant_prefix()}[no textual response]\n")
    duration_seconds = time.monotonic() - started_at
    if tools_used > 0 or warnings_seen > 0 or terminal_error:
        active_renderer.emit_run_summary(
            run_id,
            tools_used,
            warnings_seen,
            duration_seconds=duration_seconds,
        )
    if not saw_usage:
        output_tokens = len(assistant_text)
    return assistant_text, input_tokens, output_tokens, latest_pressure


__all__ = ["render_chat_stream"]
