"""Summarize one chat run into scenario-checkable quality signals."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.compaction import (
    compaction_summary as _compaction_summary,
    context_pressure_summary as _context_pressure_summary,
)
from agent_driver.observability.run_trace.planning import (
    planning_execution_expected as _planning_execution_expected,
    planning_summary as _planning_summary,
    planning_todos_incomplete as _planning_todos_incomplete,
)
from agent_driver.observability.run_trace.provider import (
    llm_call_summary as _llm_call_summary,
    prompt_surface_summary as _prompt_surface_summary,
    provider_profile_summary as _provider_profile_summary,
    provider_rejected as _provider_rejected,
)
from agent_driver.observability.run_trace.research import (
    RESEARCH_TOOLS as _RESEARCH_TOOLS,
    requires_research as _requires_research,
    research_final_answer_covers_plan_todos as _research_final_answer_covers_plan_todos,
    research_summary as _research_summary,
)
from agent_driver.observability.run_trace.tools import (
    assistant_text as _assistant_text,
    count_events as _count_events,
    event_data as _event_data,
    event_tools,
    interrupt_reasons as _interrupt_reasons,
    tool_names as _tool_names,
    tool_payloads as _tool_payloads,
    unknown_tool_summary as _unknown_tool_summary,
)
from agent_driver.runtime.single_agent.lifecycle.continuation import (
    analyze_continuation_intent,
)

_PYTHON_TOOL = "python"
_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_cancelled"})


def summarize_run_trace(
    *,
    run_id: str,
    events: list[dict[str, object]],
    user_prompt: str | None = None,
    assistant_text: str | None = None,
    task_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return compact verdicts for live chat scenario debugging."""
    tool_names = _tool_names(events)
    terminal_event = _last_event_name(events, _TERMINAL_EVENTS)
    interrupt_reasons = _interrupt_reasons(events)
    text = assistant_text if assistant_text is not None else _assistant_text(events)
    continuation = analyze_continuation_intent(text)
    requires_research = _requires_research(
        task_contract=task_contract,
        user_prompt=user_prompt,
    )
    planning = _planning_summary(events, tool_names)
    research = _research_summary(
        events,
        tool_names=tool_names,
        requires_research=requires_research,
        user_prompt=user_prompt,
        assistant_text=text,
        task_contract=task_contract,
        planning=planning,
    )
    llm_calls = _llm_call_summary(events)
    provider_profile = _provider_profile_summary(events)
    prompt_surface = _prompt_surface_summary(events)
    runtime_markers = _runtime_markers(events)
    subagents = _subagent_summary(
        events,
        tool_names=tool_names,
        user_prompt=user_prompt,
        assistant_text=text,
        continuation_reason=continuation.reason,
    )
    python = _python_summary(
        events,
        tool_names=tool_names,
        user_prompt=user_prompt,
        assistant_text=text,
        terminal_event=terminal_event,
        continuation_reason=continuation.reason,
    )
    controls = _control_summary(events)
    compaction = _compaction_summary(events)
    context_pressure = _context_pressure_summary(
        events,
        tool_names=tool_names,
        compaction=compaction,
    )
    provider_rejected = _provider_rejected(events)
    unknown_tools = _unknown_tool_summary(events)
    artifacts = _artifact_summary(events)
    research_efficiency = _research_efficiency_summary(
        events,
        tool_names=tool_names,
        assistant_text=text,
        user_prompt=user_prompt,
        task_contract=task_contract,
        requires_research=requires_research,
        research=research,
        artifacts=artifacts,
        llm_calls=llm_calls,
    )
    research_final_covers_plan = _research_final_answer_covers_plan_todos(
        requires_research=requires_research,
        research=research,
        assistant_text=text,
    )

    failures: dict[str, bool] = {
        "stuck_on_interrupt": bool(interrupt_reasons) and terminal_event is None,
        "missing_terminal_event": terminal_event is None,
        "run_failed_or_cancelled": terminal_event in {"run_failed", "run_cancelled"},
        "missing_required_research_evidence": (
            requires_research
            and not any(name in _RESEARCH_TOOLS for name in tool_names)
        ),
        "search_only_research_report": research["fetch_required_but_missing"],
        "insufficient_research_source_diversity": research[
            "insufficient_source_diversity"
        ],
        "final_missing_source_links": research["final_missing_source_links"],
        "progress_only_final": continuation.reason == "continuation_signal",
        "text_form_tool_call": continuation.reason == "text_form_tool_call",
        "fabricated_planning": planning["verdict"] == "fabricated"
        and not provider_rejected
        and _planning_execution_expected(
            requires_research=requires_research,
            user_prompt=user_prompt,
            assistant_text=text,
        ),
        "repeated_approval_planning": planning["approval_cycles"] > 1,
        "plan_todos_incomplete_on_final": (
            terminal_event == "run_completed"
            and _planning_todos_incomplete(
                planning,
                assistant_text=text,
                allow_all_todos=research_final_covers_plan,
            )
        ),
        "extra_ask_user_question": _extra_ask_user_question(
            tool_names=tool_names,
            requires_research=requires_research,
            user_prompt=user_prompt,
            assistant_text=text,
        ),
        "missed_explicit_delegation": (
            subagents["delegation_requested"] and not subagents["agent_tool_used"]
        ),
        "unnecessary_delegation": (
            subagents["agent_tool_used"] and _simple_prompt(user_prompt)
        ),
        "subagent_no_final": (
            subagents["agent_tool_used"] and not subagents["parent_synthesized_final"]
        ),
        "child_result_not_used": (
            subagents["groups_joined"] > 0 and not subagents["parent_synthesized_final"]
        ),
        "child_prompt_not_bounded": (
            subagents["agent_tool_used"] and _agent_tool_prompt_unbounded(events)
        ),
        "missed_python": False,
        "python_no_final": (
            python["python_tool_used"] and not python["final_after_python"]
        ),
        "python_policy_loop": python["python_policy_errors"] > 1,
        "unnecessary_python": False,
        "python_result_ignored": (
            python["python_tool_used"]
            and python["python_result_observed"]
            and not python["final_after_python"]
        ),
        "unknown_tool_call": unknown_tools["count"] > 0,
        "deep_research_no_report_artifact": research_efficiency[
            "missing_report_artifact"
        ],
        "deep_research_no_source_ledger_artifact": research_efficiency[
            "missing_source_ledger_artifact"
        ],
        "deep_research_full_report_rewrite": research_efficiency["full_report_rewrite"],
        "deep_research_stale_report_edit": research_efficiency["stale_report_edit"],
        "deep_research_repeated_report_read": research_efficiency[
            "repeated_report_read"
        ],
        "deep_research_final_missing_report_reference": research_efficiency[
            "final_missing_report_reference"
        ],
        "deep_research_long_final_after_report": research_efficiency[
            "long_final_after_report"
        ],
        "deep_research_missing_initial_todo": research_efficiency[
            "missing_initial_todo"
        ],
    }
    notes = _notes(
        failures=failures,
        continuation_reason=continuation.reason,
        interrupt_reasons=interrupt_reasons,
    )
    return {
        "run_id": run_id,
        "verdict": "fail" if any(failures.values()) else "pass",
        "terminal_event": terminal_event,
        "llm_calls": llm_calls["completed"],
        "llm": llm_calls,
        "provider_profile": provider_profile,
        "prompt_surface": prompt_surface,
        "tool_calls": len(tool_names),
        "tool_names": tool_names,
        "tool_chain": " -> ".join(tool_names),
        "artifacts": artifacts,
        "research_efficiency": research_efficiency,
        "runtime_markers": runtime_markers,
        "research": {
            "required": requires_research,
            "tools_used": [name for name in tool_names if name in _RESEARCH_TOOLS],
            **research,
        },
        "python": python,
        "planning": planning,
        "subagents": subagents,
        "controls": controls,
        "compaction": compaction,
        "context_pressure": context_pressure,
        "unknown_tools": unknown_tools,
        "provider_rejected": provider_rejected,
        "final_readiness": research["final_readiness"],
        "repair_required_reasons": research["repair_required_reasons"],
        "interrupts": interrupt_reasons,
        "continuation_reason": continuation.reason,
        "failures": failures,
        "notes": notes,
    }


def _last_event_name(
    events: list[dict[str, object]],
    names: frozenset[str],
) -> str | None:
    for event in reversed(events):
        name = event.get("event")
        if isinstance(name, str) and name in names:
            return name
    return None


def _subagent_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    user_prompt: str | None,
    assistant_text: str,
    continuation_reason: str | None,
) -> dict[str, Any]:
    statuses: list[str] = []
    join_states: list[str] = []
    for event in events:
        data = _event_data(event)
        if event.get("event") == "subagent_completed":
            status = data.get("status")
            if isinstance(status, str) and status:
                statuses.append(status)
        if event.get("event") in {"subagent_group_joined", "subagent_group_failed"}:
            join_state = data.get("join_state")
            if isinstance(join_state, str) and join_state:
                join_states.append(join_state)
    agent_tool_used = "agent_tool" in tool_names
    groups_joined = _count_events(events, "subagent_group_joined")
    child_error_count = sum(
        1
        for status in statuses
        if status.lower() in {"failed", "error", "cancelled", "timeout"}
    )
    parent_synthesized_final = (
        agent_tool_used
        and groups_joined > 0
        and continuation_reason != "continuation_signal"
        and not _subagent_progress_only_text(assistant_text)
        and len(assistant_text.strip()) >= 20
    )
    return {
        "delegation_requested": _delegation_requested(user_prompt),
        "delegation_expected": _delegation_requested(user_prompt),
        "agent_tool_used": agent_tool_used,
        "groups_started": _count_events(events, "subagent_group_started"),
        "groups_joined": groups_joined,
        "groups_failed": _count_events(events, "subagent_group_failed"),
        "runs_started": _count_events(events, "subagent_started"),
        "runs_completed": _count_events(events, "subagent_completed"),
        "child_error_count": child_error_count,
        "parent_synthesized_final": parent_synthesized_final,
        "statuses": statuses,
        "join_states": join_states,
    }


def _python_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    user_prompt: str | None,
    assistant_text: str,
    terminal_event: str | None,
    continuation_reason: str | None,
) -> dict[str, Any]:
    payloads = _tool_payloads(events, _PYTHON_TOOL)
    completed_payloads = [
        payload
        for payload in payloads
        if str(payload.get("status") or "").lower()
        in {"completed", "done", "success", "ok"}
    ]
    result_texts = [
        str(payload.get("result_summary") or payload.get("result") or "")
        for payload in payloads
    ]
    combined_results = "\n".join(result_texts).lower()
    python_tool_used = _PYTHON_TOOL in tool_names
    final_after_python = (
        python_tool_used
        and terminal_event == "run_completed"
        and continuation_reason != "continuation_signal"
        and len(assistant_text.strip()) >= 3
    )
    return {
        "python_tool_available": python_tool_used,
        "python_tool_used": python_tool_used,
        "python_calls": tool_names.count(_PYTHON_TOOL),
        "python_policy_errors": sum(
            1
            for text in result_texts
            if "python policy:" in text.lower() or "unauthorized import" in text.lower()
        ),
        "python_timeouts": sum(1 for text in result_texts if "timeout" in text.lower()),
        "python_expected": False,
        "missed_python_for_calculation": False,
        "python_result_observed": bool(
            completed_payloads
            or any(text.strip() for text in result_texts)
            or "final_answer" in combined_results
        ),
        "final_after_python": final_after_python,
        "final_mentions_python_error": any(
            marker in assistant_text.lower()
            for marker in ("python policy", "unauthorized import", "sandbox")
        ),
    }


def _delegation_requested(user_prompt: str | None) -> bool:
    text = " ".join((user_prompt or "").lower().split())
    return any(
        marker in text
        for marker in (
            "субагент",
            "дочерн",
            "делегир",
            "поручи",
            "отдельный агент",
            "subagent",
            "delegate",
            "child agent",
            "worker agent",
        )
    )


def _simple_prompt(user_prompt: str | None) -> bool:
    text = " ".join((user_prompt or "").lower().split())
    if not text:
        return False
    if _delegation_requested(text) or _requires_research(
        task_contract=None,
        user_prompt=text,
    ):
        return False
    complex_markers = (
        "сравни",
        "проверь",
        "проанализируй",
        "реферат",
        "план",
        "compare",
        "review",
        "analyze",
        "report",
        "plan",
    )
    if any(marker in text for marker in complex_markers):
        return False
    return len(text.split()) <= 8


def _subagent_progress_only_text(assistant_text: str) -> bool:
    text = " ".join(assistant_text.lower().split())
    progress_markers = (
        "сейчас подготовлю",
        "сейчас составлю",
        "теперь подготовлю",
        "теперь составлю",
        "приступаю к итог",
        "subagent completed",
        "now i will prepare",
        "now i will write",
        "i will now synthesize",
    )
    return any(marker in text for marker in progress_markers)


def _agent_tool_prompt_unbounded(events: list[dict[str, object]]) -> bool:
    saw_agent_tool = False
    for event in events:
        if event.get("event") not in {"tool_call_started", "tool_call_completed"}:
            continue
        data = _event_data(event)
        tool_payloads: list[dict[str, Any]] = []
        if data.get("tool_name") == "agent_tool":
            tool_payloads.append(data)
        tools = data.get("tools")
        if isinstance(tools, list):
            tool_payloads.extend(
                tool
                for tool in tools
                if isinstance(tool, dict)
                and (
                    tool.get("tool_name") == "agent_tool"
                    or tool.get("name") == "agent_tool"
                )
            )
        for tool in tool_payloads:
            saw_agent_tool = True
            args = tool.get("args")
            if not isinstance(args, dict):
                continue
            task = str(args.get("task") or "").strip()
            description = str(args.get("description") or "").strip()
            if len(task.split()) >= 5 and description:
                return False
    return saw_agent_tool


def _control_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    kinds: list[str] = []
    priorities: list[str] = []
    semantic_routes: list[str] = []
    for event in events:
        if event.get("event") not in {
            "control_requested",
            "command_queued",
            "command_dequeued",
            "control_applied",
            "command_cancelled",
        }:
            continue
        kind = _event_data(event).get("kind")
        if isinstance(kind, str) and kind:
            kinds.append(kind)
        priority = _event_data(event).get("priority")
        if isinstance(priority, str) and priority:
            priorities.append(priority)
        route = _control_semantic_route(kind=kind, priority=priority)
        if route:
            semantic_routes.append(route)
    return {
        "requested": _count_events(events, "control_requested"),
        "queued": _count_events(events, "command_queued"),
        "dequeued": _count_events(events, "command_dequeued"),
        "applied": _count_events(events, "control_applied"),
        "cancelled": _count_events(events, "command_cancelled"),
        "kinds": kinds,
        "priorities": priorities,
        "semantic_routes": sorted(set(semantic_routes)),
    }


def _control_semantic_route(kind: object, priority: object) -> str | None:
    if not isinstance(kind, str) or not kind:
        return None
    priority_value = priority if isinstance(priority, str) else ""
    fixed_routes = {
        "interrupt": "interrupt_now",
        "patch_planning_state": "runtime_setting",
        "stop_subagent": "subagent_control",
        "continue_subagent": "subagent_control",
    }
    route = fixed_routes.get(kind)
    if route is not None:
        return route
    if kind == "enqueue_user_message":
        if priority_value == "now":
            route = "steer_at_next_boundary"
        elif priority_value == "later":
            route = "queue_later"
        else:
            route = "queue_after_next_boundary"
    elif kind.startswith("set_"):
        route = "runtime_setting"
    else:
        route = kind
    return route


def _artifact_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    updates: list[dict[str, Any]] = []
    report_paths: list[str] = []
    source_ledger_paths: list[str] = []
    source_ledger_records = 0
    for event in events:
        if event.get("event") not in {"artifact_created", "artifact_updated"}:
            continue
        data = _event_data(event)
        path = data.get("path")
        if not isinstance(path, str) or not path:
            continue
        operation = data.get("operation")
        kind = data.get("kind")
        updates.append(
            {
                "event": event.get("event"),
                "path": path,
                "kind": kind if isinstance(kind, str) else None,
                "operation": operation if isinstance(operation, str) else None,
                "mode": data.get("mode") if isinstance(data.get("mode"), str) else None,
                "size_bytes": data.get("size_bytes", data.get("bytes")),
                "tool_name": data.get("tool_name"),
                "tool_call_id": data.get("tool_call_id"),
            }
        )
        if path == "research/report.md":
            report_paths.append(path)
        if path == "research/sources.jsonl":
            source_ledger_paths.append(path)
            record_count = data.get("record_count")
            if isinstance(record_count, int) and not isinstance(record_count, bool):
                source_ledger_records = max(source_ledger_records, record_count)
    return {
        "updates": updates,
        "update_count": len(updates),
        "created_count": _count_events(events, "artifact_created"),
        "updated_count": _count_events(events, "artifact_updated"),
        "paths": _dedupe_paths([item["path"] for item in updates]),
        "report_updated": bool(report_paths),
        "report_update_count": len(report_paths),
        "report_full_write_count": _report_full_write_count(updates),
        **_report_read_edit_flow_summary(events),
        "source_ledger_updated": bool(source_ledger_paths),
        "source_ledger_update_count": len(source_ledger_paths),
        "source_ledger_record_count": source_ledger_records,
    }


def _research_efficiency_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    assistant_text: str,
    user_prompt: str | None,
    task_contract: dict[str, Any] | None,
    requires_research: bool,
    research: dict[str, Any],
    artifacts: dict[str, Any],
    llm_calls: dict[str, Any],
) -> dict[str, Any]:
    deep_expected = _deep_research_artifact_expected(
        user_prompt=user_prompt,
        task_contract=task_contract,
        requires_research=requires_research,
        research=research,
    )
    report_updated = bool(artifacts.get("report_updated"))
    report_full_write_count = _as_int(artifacts.get("report_full_write_count"))
    stale_report_edit_count = _as_int(
        artifacts.get("report_targeted_edit_without_fresh_read_count")
    )
    repeated_report_read_count = _as_int(
        artifacts.get("repeated_unchanged_report_read_count")
    )
    source_ledger_updated = bool(artifacts.get("source_ledger_updated"))
    assistant_chars = len(assistant_text.strip())
    usage = llm_calls.get("usage")
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else 0
    output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else 0
    completion_after_report = _output_tokens_after_first_report_update(events)
    first_tool = tool_names[0] if tool_names else None
    missing_initial_todo = deep_expected and first_tool not in {None, "todo_write"}
    long_final_after_report = report_updated and assistant_chars > 2000
    missing_report_artifact = deep_expected and not report_updated
    missing_source_ledger_artifact = deep_expected and not source_ledger_updated
    full_report_rewrite = deep_expected and report_full_write_count > 1
    stale_report_edit = deep_expected and stale_report_edit_count > 0
    repeated_report_read = deep_expected and repeated_report_read_count > 0
    final_references_report = _final_references_report_artifact(assistant_text)
    final_missing_report_reference = (
        deep_expected and report_updated and not final_references_report
    )
    repeated_tools = sorted(
        name
        for name in set(tool_names)
        if tool_names.count(name) > 1 and name not in {"web_search", "web_fetch"}
    )
    return {
        "deep_research_artifact_expected": deep_expected,
        "missing_report_artifact": missing_report_artifact,
        "missing_source_ledger_artifact": missing_source_ledger_artifact,
        "full_report_rewrite": full_report_rewrite,
        "stale_report_edit": stale_report_edit,
        "repeated_report_read": repeated_report_read,
        "final_references_report_artifact": final_references_report,
        "final_missing_report_reference": final_missing_report_reference,
        "missing_initial_todo": missing_initial_todo,
        "first_tool": first_tool,
        "tool_chain": " -> ".join(tool_names),
        "unique_tool_count": len(set(tool_names)),
        "repeated_tools": repeated_tools,
        "artifact_update_count": artifacts.get("update_count", 0),
        "report_update_count": artifacts.get("report_update_count", 0),
        "report_full_write_count": report_full_write_count,
        "report_targeted_edit_count": artifacts.get("report_targeted_edit_count", 0),
        "report_targeted_edit_without_fresh_read_count": stale_report_edit_count,
        "repeated_unchanged_report_read_count": repeated_report_read_count,
        "source_ledger_update_count": artifacts.get("source_ledger_update_count", 0),
        "source_ledger_record_count": artifacts.get("source_ledger_record_count", 0),
        "long_final_after_report": long_final_after_report,
        "assistant_chars": assistant_chars,
        "total_tokens": total_tokens if isinstance(total_tokens, int) else 0,
        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
        "output_tokens_after_first_report_update": completion_after_report,
    }


def _deep_research_artifact_expected(
    *,
    user_prompt: str | None,
    task_contract: dict[str, Any] | None,
    requires_research: bool,
    research: dict[str, Any],
) -> bool:
    if isinstance(task_contract, dict):
        if task_contract.get("deep_research") is True:
            return True
        if task_contract.get("artifact_required") is True:
            return True
    text = " ".join((user_prompt or "").lower().split())
    if any(
        marker in text
        for marker in (
            "deep research",
            "глубок",
            "research report",
            "long report",
        )
    ):
        return requires_research
    return False


def _final_references_report_artifact(assistant_text: str) -> bool:
    text = " ".join((assistant_text or "").lower().split())
    if not text:
        return False
    markers = (
        "research/report.md",
        "report.md",
        "отчет сохранен",
        "отчёт сохранен",
        "отчет в",
        "отчёт в",
        "полный отчет",
        "полный отчёт",
        "full report",
        "saved in",
        "saved to",
    )
    return any(marker in text for marker in markers)


def _output_tokens_after_first_report_update(events: list[dict[str, object]]) -> int:
    saw_report = False
    output_tokens = 0
    for event in events:
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            data = _event_data(event)
            if data.get("path") == "research/report.md":
                saw_report = True
            continue
        if not saw_report or event.get("event") != "llm_call_completed":
            continue
        data = _event_data(event)
        usage = data.get("usage")
        if not isinstance(usage, dict):
            continue
        value = usage.get("output_tokens", usage.get("completion_tokens"))
        if isinstance(value, bool):
            continue
        if isinstance(value, int):
            output_tokens += max(0, value)
        elif isinstance(value, float):
            output_tokens += max(0, int(value))
    return output_tokens


def _report_full_write_count(updates: list[dict[str, Any]]) -> int:
    count = 0
    for item in updates:
        if item.get("path") != "research/report.md":
            continue
        if item.get("tool_name") != "file_write":
            continue
        mode = item.get("mode")
        if mode == "append":
            continue
        count += 1
    return count


def _report_read_edit_flow_summary(events: list[dict[str, object]]) -> dict[str, int]:
    fresh_read = False
    targeted_edits = 0
    stale_targeted_edits = 0
    report_reads = 0
    repeated_unchanged_reads = 0
    report_generation = 0
    read_generations: set[int] = set()
    for action in _report_flow_actions(events):
        if action["kind"] == "read":
            report_reads += 1
            if report_generation in read_generations:
                repeated_unchanged_reads += 1
            read_generations.add(report_generation)
            fresh_read = True
            continue
        tool_name = action.get("tool_name")
        if tool_name == "file_write":
            report_generation += 1
            fresh_read = False
        elif tool_name in {"file_edit", "file_patch"}:
            targeted_edits += 1
            if not fresh_read:
                stale_targeted_edits += 1
            report_generation += 1
            fresh_read = False
    return {
        "report_read_count": report_reads,
        "repeated_unchanged_report_read_count": repeated_unchanged_reads,
        "report_targeted_edit_count": targeted_edits,
        "report_targeted_edit_without_fresh_read_count": stale_targeted_edits,
    }


def _report_flow_actions(events: list[dict[str, object]]) -> list[dict[str, str]]:
    updates_by_call_id: dict[str, list[dict[str, str]]] = {}
    for event in events:
        if event.get("event") not in {"artifact_created", "artifact_updated"}:
            continue
        data = _event_data(event)
        if data.get("path") != "research/report.md":
            continue
        tool_name = data.get("tool_name")
        if not isinstance(tool_name, str):
            continue
        action = {"kind": "write", "tool_name": tool_name}
        call_id = data.get("tool_call_id")
        if isinstance(call_id, str) and call_id:
            updates_by_call_id.setdefault(call_id, []).append(action)

    actions: list[dict[str, str]] = []
    paired_update_ids: set[int] = set()
    for event in events:
        if event.get("event") in {"artifact_created", "artifact_updated"}:
            data = _event_data(event)
            if data.get("path") != "research/report.md":
                continue
            call_id = data.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                continue
            tool_name = data.get("tool_name")
            if isinstance(tool_name, str):
                actions.append({"kind": "write", "tool_name": tool_name})
            continue
        if event.get("event") != "tool_call_completed":
            continue
        for tool in event_tools(_event_data(event)):
            tool_name = tool.get("tool_name") or tool.get("name")
            if tool_name in {"read_file", "artifact_read", "artifact_preview"}:
                args = tool.get("args")
                if isinstance(args, dict) and _path_targets_report(args.get("path")):
                    actions.append({"kind": "read", "tool_name": str(tool_name)})
            call_id = tool.get("tool_call_id")
            if isinstance(call_id, str) and call_id:
                for update in updates_by_call_id.get(call_id, []):
                    actions.append(update)
                    paired_update_ids.add(id(update))

    for updates in updates_by_call_id.values():
        for update in updates:
            if id(update) not in paired_update_ids:
                actions.append(update)
    return actions


def _path_targets_report(value: object) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().replace("\\", "/").rstrip("/")
    return normalized == "research/report.md" or normalized.endswith(
        "/research/report.md"
    )


def _as_int(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    return 0


def _dedupe_paths(paths: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for path in paths:
        if path in seen:
            continue
        seen.add(path)
        result.append(path)
    return result


def _runtime_markers(events: list[dict[str, object]]) -> dict[str, list[str]]:
    force_final_reasons: list[str] = []
    continuation_reasons: list[str] = []
    for event in events:
        data = _event_data(event)
        force_final_reason = data.get("force_final_reason")
        if isinstance(force_final_reason, str) and force_final_reason:
            force_final_reasons.append(force_final_reason)
        continuation_reason = data.get("continuation_reason")
        if isinstance(continuation_reason, str) and continuation_reason:
            continuation_reasons.append(continuation_reason)
    return {
        "force_final_reasons": sorted(set(force_final_reasons)),
        "continuation_reasons": sorted(set(continuation_reasons)),
    }


def _extra_ask_user_question(
    *,
    tool_names: list[str],
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
) -> bool:
    if "ask_user_question" not in tool_names:
        return False
    prompt = " ".join((user_prompt or "").lower().split())
    deliverable_markers = (
        "напиши",
        "черновик",
        "реферат",
        "final answer",
        "write",
        "draft",
    )
    if requires_research or any(marker in prompt for marker in deliverable_markers):
        return True
    return "?" not in assistant_text and "？" not in assistant_text


_FAILURE_NOTE_MESSAGES = (
    (
        "missing_required_research_evidence",
        "Research was required, but no web_search/web_fetch tool call is visible.",
    ),
    (
        "search_only_research_report",
        "Report-like research stopped after search results without enough fetched sources.",
    ),
    (
        "insufficient_research_source_diversity",
        "Report-like research fetched sources but did not cover enough distinct domains.",
    ),
    (
        "final_missing_source_links",
        "Source-verified research produced a final answer without concrete source links.",
    ),
    (
        "deep_research_no_report_artifact",
        "Deep Research/report-like task finished without a research/report.md artifact update.",
    ),
    (
        "deep_research_no_source_ledger_artifact",
        "Deep Research finished without a durable research/sources.jsonl ledger.",
    ),
    (
        "deep_research_full_report_rewrite",
        "research/report.md was fully written more than once; use file_edit/file_patch after the first draft.",
    ),
    (
        "deep_research_stale_report_edit",
        "research/report.md was edited without a fresh read/preview after the previous write.",
    ),
    (
        "deep_research_repeated_report_read",
        "research/report.md was read repeatedly without an intervening artifact update.",
    ),
    (
        "deep_research_final_missing_report_reference",
        "Final Deep Research handoff did not point the user to research/report.md.",
    ),
    (
        "deep_research_long_final_after_report",
        "Final answer is long even though research/report.md was already available.",
    ),
    (
        "deep_research_missing_initial_todo",
        "Deep Research did not start with todo_write before data/artifact tools.",
    ),
    (
        "plan_todos_incomplete_on_final",
        "Run completed while the visible plan still had unfinished checklist items.",
    ),
    (
        "progress_only_final",
        "Final assistant text looks like progress narration, not a deliverable.",
    ),
    (
        "text_form_tool_call",
        "Assistant emitted a plain-text tool call instead of native tool-call JSON.",
    ),
    ("fabricated_planning", "Planning tools ran, but no data/execution tool followed."),
    (
        "repeated_approval_planning",
        "Approval planning was entered more than once in one run.",
    ),
    (
        "extra_ask_user_question",
        "ask_user_question was used where the task should proceed with assumptions.",
    ),
    (
        "missed_explicit_delegation",
        "User explicitly requested delegation, but agent_tool was not used.",
    ),
    (
        "unnecessary_delegation",
        "agent_tool was used for a simple prompt that should stay direct.",
    ),
    (
        "subagent_no_final",
        "Subagent delegation happened, but parent did not synthesize a final answer.",
    ),
    (
        "child_result_not_used",
        "A subagent group joined, but the parent answer does not show synthesis.",
    ),
    (
        "child_prompt_not_bounded",
        "agent_tool was called without a bounded child task and description.",
    ),
    (
        "missed_python",
        "A calculation/counting prompt should have used the python tool.",
    ),
    (
        "python_no_final",
        "Python ran, but the assistant did not produce a terminal final answer.",
    ),
    (
        "python_policy_loop",
        "Python hit repeated policy errors instead of rewriting with allowed imports.",
    ),
    (
        "unnecessary_python",
        "Python ran for a simple prompt where code execution was unnecessary.",
    ),
    (
        "python_result_ignored",
        "Python returned a result, but the final answer did not use it.",
    ),
)


def _notes(
    *,
    failures: dict[str, bool],
    continuation_reason: str | None,
    interrupt_reasons: list[str],
) -> list[str]:
    notes: list[str] = []
    notes.extend(message for key, message in _FAILURE_NOTE_MESSAGES if failures[key])
    if interrupt_reasons:
        notes.append("Run paused for interrupt: " + ", ".join(interrupt_reasons))
    if continuation_reason and not any(notes):
        notes.append(f"Continuation detector reason: {continuation_reason}.")
    return notes


__all__ = ["summarize_run_trace"]
