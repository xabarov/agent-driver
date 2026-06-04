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
_DEEP_RESEARCH_INITIAL_SEARCH_BUDGET = 6
_DEEP_RESEARCH_HARD_SEARCH_CAP = 15
_DEEP_RESEARCH_PHASE_FETCH_ATTEMPTS = 2
_DEEP_RESEARCH_LONG_CHAT_BEFORE_REPORT_CHARS = 1_500
_READ_SOURCE_TOOLS = frozenset({"web_fetch", "source_read", "pdf_read", "browser_read"})
_PARENT_SYNTHESIS_TOOLS = frozenset(
    {
        "file_write",
        "todo_write",
        *_READ_SOURCE_TOOLS,
    }
)
_DEEP_RESEARCH_PHASE_ALLOWED_TOOLS: dict[str, frozenset[str]] = {
    "plan": frozenset({"todo_write", "skill_tool", "skill_view"}),
    "discover": frozenset(
        {
            "agent_tool",
            "skill_tool",
            "skill_view",
            "web_search",
            *_READ_SOURCE_TOOLS,
            "glob_search",
            "grep_search",
            "read_file",
            "todo_write",
        }
    ),
    "verify": frozenset(
        {"agent_tool", "web_search", "read_file", "todo_write", *_READ_SOURCE_TOOLS}
    ),
    "write": frozenset(
        {
            "file_write",
            "file_edit",
            "file_patch",
            "read_file",
            *_READ_SOURCE_TOOLS,
            "artifact_list",
            "artifact_read",
            "artifact_preview",
            "todo_write",
        }
    ),
    "review": frozenset(
        {
            "artifact_list",
            "artifact_preview",
            "artifact_read",
            "read_file",
            "file_patch",
            "file_edit",
            *_READ_SOURCE_TOOLS,
            "todo_write",
        }
    ),
}


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
        task_contract=task_contract,
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
    deep_research_requires_parent_synthesis = (
        _deep_research_expected_from_contract(task_contract)
        and _deep_research_max_subagent_requests_from_contract(
            task_contract=task_contract,
            user_prompt=user_prompt,
        )
        > 0
    )
    deep_research_artifact_handoff_complete = (
        terminal_event == "run_completed"
        and bool(research_efficiency.get("deep_research_artifact_expected"))
        and bool(artifacts.get("report_write_seen"))
        and bool(artifacts.get("source_ledger_updated"))
        and _as_int(artifacts.get("source_ledger_record_count")) > 0
        and bool(research_efficiency.get("final_references_report_artifact"))
        and (
            not deep_research_requires_parent_synthesis
            or bool(subagents.get("parent_synthesized_final"))
        )
    )

    failures: dict[str, bool] = {
        "stuck_on_interrupt": bool(interrupt_reasons) and terminal_event is None,
        "missing_terminal_event": terminal_event is None,
        "run_failed_or_cancelled": terminal_event in {"run_failed", "run_cancelled"},
        "missing_required_research_evidence": (
            requires_research
            and not any(name in _RESEARCH_TOOLS for name in tool_names)
            and not deep_research_artifact_handoff_complete
        ),
        "search_only_research_report": research["fetch_required_but_missing"]
        and not deep_research_artifact_handoff_complete,
        "insufficient_research_source_diversity": research[
            "insufficient_source_diversity"
        ],
        "final_missing_source_links": research["final_missing_source_links"],
        "progress_only_final": continuation.reason == "continuation_signal",
        "text_form_tool_call": continuation.reason == "text_form_tool_call"
        and not deep_research_artifact_handoff_complete,
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
            and not deep_research_artifact_handoff_complete
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
        ]
        and not deep_research_artifact_handoff_complete,
        "deep_research_missing_initial_todo": research_efficiency[
            "missing_initial_todo"
        ],
        "deep_research_unexpected_agent_tool": research_efficiency[
            "unexpected_agent_tool"
        ],
        "deep_research_skill_denied": research_efficiency["skill_denied"],
        "deep_research_low_verified_coverage": research_efficiency[
            "low_verified_coverage"
        ]
        and not deep_research_artifact_handoff_complete,
        "deep_research_preliminary_final": research_efficiency["preliminary_final"]
        and not deep_research_artifact_handoff_complete,
        "deep_research_repeated_search_args": research_efficiency[
            "repeated_search_args"
        ],
        "deep_research_search_without_fetch_progress": research_efficiency[
            "search_without_fetch_progress"
        ],
        "deep_research_tool_entropy_high": research_efficiency["tool_entropy_high"],
        "deep_research_phase_violation": research_efficiency["phase_violation"],
        "deep_research_browser_action_without_opt_in": research_efficiency[
            "hard_browser_action_without_opt_in"
        ],
        "deep_research_browser_used_before_source_read": research_efficiency[
            "hard_browser_used_before_source_read"
        ],
        "deep_research_browser_read_missing_fallback_reason": research_efficiency[
            "hard_browser_read_missing_fallback_reason"
        ],
        "deep_research_hard_claims_missing": research_efficiency["hard_claims_missing"],
        "deep_research_hard_claims_empty": research_efficiency["hard_claims_empty"],
        "deep_research_hard_claims_no_verified": research_efficiency[
            "hard_claims_no_verified"
        ],
        "deep_research_hard_claims_unsupported": research_efficiency[
            "hard_claims_unsupported"
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
        "deep_research_artifact_handoff_complete": deep_research_artifact_handoff_complete,
        "runtime_markers": runtime_markers,
        "research": {
            "required": requires_research,
            "tools_used": [name for name in tool_names if name in _RESEARCH_TOOLS],
            **_rollup_research_with_children(research, _child_evidence_summary(events)),
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


def _tool_count(tool_names: list[str], names: frozenset[str]) -> int:
    return sum(1 for name in tool_names if name in names)


def _subagent_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    user_prompt: str | None,
    task_contract: dict[str, Any] | None,
    assistant_text: str,
    continuation_reason: str | None,
) -> dict[str, Any]:
    statuses: list[str] = []
    join_states: list[str] = []
    child_synthesis_pending = False
    child_synthesis_summary_chars = 0
    marker_seen = False
    child_join_seen = False
    parent_report_write_seen_after_child = False
    parent_report_write_seen_after_marker = False
    parent_report_artifact_after_child_join = False
    source_ledger_artifact_after_marker = False
    runs_started_before_child_synthesis = 0
    tools_after_child_synthesis_pending: list[str] = []
    for event in events:
        data = _event_data(event)
        event_name = event.get("event")
        if event_name in {"subagent_group_joined", "subagent_group_failed"}:
            child_join_seen = True
        if event_name == "tool_call_completed":
            for tool in event_tools(data):
                if child_join_seen and _tool_is_parent_report_write(tool):
                    parent_report_write_seen_after_child = True
        if (
            child_join_seen
            and event_name in {"artifact_created", "artifact_updated"}
            and _artifact_event_is_parent_report_write(data)
        ):
            parent_report_write_seen_after_child = True
            parent_report_artifact_after_child_join = True
        if (
            marker_seen
            and not parent_report_write_seen_after_marker
            and event_name == "tool_call_completed"
        ):
            for tool in event_tools(data):
                if _is_parent_synthesis_gate_denial(tool):
                    continue
                tool_name = tool.get("tool_name") or tool.get("name")
                if isinstance(tool_name, str) and tool_name:
                    tools_after_child_synthesis_pending.append(tool_name)
                if _tool_is_parent_report_write(tool):
                    parent_report_write_seen_after_marker = True
        if (
            marker_seen
            and not parent_report_write_seen_after_marker
            and event_name in {"artifact_created", "artifact_updated"}
            and _artifact_event_is_parent_report_write(data)
        ):
            parent_report_write_seen_after_marker = True
        if (
            marker_seen
            and event_name in {"artifact_created", "artifact_updated"}
            and _artifact_event_is_source_ledger_write(data)
        ):
            source_ledger_artifact_after_marker = True
        if event_name == "subagent_started" and not marker_seen:
            runs_started_before_child_synthesis += 1
        if event_name == "subagent_completed":
            status = data.get("status")
            if isinstance(status, str) and status:
                statuses.append(status)
        if event_name in {"subagent_group_joined", "subagent_group_failed"}:
            join_state = data.get("join_state")
            if isinstance(join_state, str) and join_state:
                join_states.append(join_state)
        if event_name == "research_progress":
            kind = data.get("kind")
            if kind == "deep_research_child_synthesis_pending":
                child_synthesis_pending = data.get("pending") is True
                marker_seen = True
                raw_chars = data.get("summary_chars")
                if isinstance(raw_chars, int) and not isinstance(raw_chars, bool):
                    child_synthesis_summary_chars = max(
                        child_synthesis_summary_chars,
                        raw_chars,
                    )
    agent_tool_used = "agent_tool" in tool_names
    groups_joined = _count_events(events, "subagent_group_joined")
    max_subagent_requests = _deep_research_max_subagent_requests_from_contract(
        task_contract=task_contract,
        user_prompt=user_prompt,
    )
    unexpected_tool_after_child_synthesis = _unexpected_tool_after_child_synthesis(
        tools_after_child_synthesis_pending,
        runs_started_before_child_synthesis=runs_started_before_child_synthesis,
        max_subagent_requests=max_subagent_requests,
    )
    child_error_count = sum(
        1
        for status in statuses
        if status.lower() in {"failed", "error", "cancelled", "timeout"}
    )
    child_evidence = _child_evidence_summary(events)
    child_metrics = _child_orchestration_metrics(
        events,
        child_evidence=child_evidence,
    )
    deep_research_expected = _deep_research_expected_from_contract(task_contract)
    parent_report_write_clears_child_synthesis = (
        parent_report_write_seen_after_child or parent_report_write_seen_after_marker
    )
    parent_artifact_handoff_after_child_marker = (
        deep_research_expected
        and bool(child_synthesis_summary_chars)
        and any(name == "file_write" for name in tools_after_child_synthesis_pending)
        and parent_report_artifact_after_child_join
        and source_ledger_artifact_after_marker
    )
    parent_synthesized_final = (
        agent_tool_used
        and groups_joined > 0
        and (
            parent_report_write_clears_child_synthesis
            or parent_artifact_handoff_after_child_marker
            or (
                not deep_research_expected
                and continuation_reason != "continuation_signal"
                and not _subagent_progress_only_text(assistant_text)
                and len(assistant_text.strip()) >= 20
            )
        )
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
        **child_metrics,
        "child_error_count": child_error_count,
        "child_search_count": child_evidence["search_count"],
        "child_fetch_count": child_evidence["fetch_count"],
        "child_verified_read_count": child_evidence["verified_read_count"],
        "parent_synthesized_final": parent_synthesized_final,
        "child_synthesis_pending": (
            child_synthesis_pending and not parent_report_write_clears_child_synthesis
        ),
        "child_synthesis_summary_chars": child_synthesis_summary_chars,
        "tools_after_child_synthesis_pending": tools_after_child_synthesis_pending,
        "first_tool_after_child_synthesis_pending": (
            tools_after_child_synthesis_pending[0]
            if tools_after_child_synthesis_pending
            else None
        ),
        "unexpected_tool_after_child_synthesis_pending": (
            unexpected_tool_after_child_synthesis
        ),
        "statuses": statuses,
        "join_states": join_states,
    }


def _tool_is_parent_report_write(tool: dict[str, Any]) -> bool:
    if not _tool_payload_succeeded(tool):
        return False
    tool_name = tool.get("tool_name") or tool.get("name")
    if tool_name not in {"file_write", "file_edit", "file_patch"}:
        return False
    args = tool.get("args")
    if not isinstance(args, dict):
        return False
    return _path_targets_report(args.get("path") or args.get("file_path"))


def _tool_payload_succeeded(payload: dict[str, Any]) -> bool:
    if payload.get("error"):
        return False
    decision = str(payload.get("decision") or "").strip().lower()
    if decision in {"deny", "denied", "interrupt", "rejected"}:
        return False
    status = str(payload.get("status") or "").strip().lower()
    if status in {"denied", "failed", "error", "timed_out", "timeout", "cancelled"}:
        return False
    return True


def _artifact_event_is_parent_report_write(data: dict[str, Any]) -> bool:
    return data.get("path") == "research/report.md" and data.get("tool_name") in {
        "file_write",
        "file_edit",
        "file_patch",
    }


def _artifact_event_is_source_ledger_write(data: dict[str, Any]) -> bool:
    return data.get("path") == "research/sources.jsonl" and data.get("tool_name") in {
        "file_write",
        "file_edit",
        "file_patch",
        "source_ledger",
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


def _unexpected_tool_after_child_synthesis(
    tool_names: list[str],
    *,
    runs_started_before_child_synthesis: int,
    max_subagent_requests: int,
) -> str | None:
    remaining_agent_tool_allowance = max(
        0,
        max_subagent_requests - max(0, runs_started_before_child_synthesis),
    )
    for name in tool_names:
        if name in _PARENT_SYNTHESIS_TOOLS:
            continue
        if name == "agent_tool" and remaining_agent_tool_allowance > 0:
            remaining_agent_tool_allowance -= 1
            continue
        return name
    return None


def _is_parent_synthesis_gate_denial(tool: dict[str, Any]) -> bool:
    status = str(tool.get("status") or "").lower()
    error_code = str(tool.get("error_code") or "").lower()
    summary = str(tool.get("result_summary") or "").lower()
    remediation = str(tool.get("remediation") or "").lower()
    return (
        status == "denied"
        and error_code == "policy_denied"
        and (
            "deep_research_parent_synthesis_gate" in summary
            or "deep_research_parent_synthesis_gate" in remediation
        )
    )


def _deep_research_max_subagent_requests_from_contract(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> int:
    if isinstance(task_contract, dict):
        raw = task_contract.get("max_subagent_requests")
        if isinstance(raw, int) and not isinstance(raw, bool):
            return max(0, raw)
        profile = task_contract.get("research_profile")
        if isinstance(profile, str):
            return _deep_research_max_subagent_requests_for_profile(profile)
    text = " ".join((user_prompt or "").lower().split())
    if "hard deep research" in text or "hard research" in text:
        return 4
    if "light deep research" in text or "light research" in text:
        return 0
    return 1


def _deep_research_max_subagent_requests_for_profile(profile: str) -> int:
    normalized = profile.strip().lower()
    if normalized == "light":
        return 0
    if normalized == "hard":
        return 4
    return 1


def _deep_research_expected_from_contract(task_contract: dict[str, Any] | None) -> bool:
    if not isinstance(task_contract, dict):
        return False
    depth = task_contract.get("research_depth")
    profile = str(task_contract.get("research_profile") or "").strip().lower()
    return (
        task_contract.get("research_mode") == "deep"
        or depth == "deep_parallel_research"
        or (depth == "source_verified_report" and profile in {"medium", "hard"})
    )


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
            task = str(
                args.get("task")
                or args.get("instructions")
                or args.get("prompt")
                or args.get("query")
                or ""
            ).strip()
            description = str(args.get("description") or "").strip()
            if len(task.split()) >= 5 and (
                description or _bounded_research_task_text(task)
            ):
                return False
    return saw_agent_tool


def _bounded_research_task_text(task: str) -> bool:
    text = task.lower()
    evidence_markers = (
        "source",
        "sources",
        "url",
        "urls",
        "источник",
        "источники",
    )
    output_markers = (
        "notes",
        "report",
        "summary",
        "summarize",
        "parent",
        "замет",
        "отчет",
        "отчёт",
        "свод",
        "родител",
    )
    return any(marker in text for marker in evidence_markers) and any(
        marker in text for marker in output_markers
    )


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
    claims_paths: list[str] = []
    source_ledger_records = 0
    claims_records = 0
    claims_verified = 0
    claims_unsupported = 0
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
        if path in {"research/claims.jsonl", "research/claims.md"}:
            claims_paths.append(path)
            record_count = data.get("record_count")
            if isinstance(record_count, int) and not isinstance(record_count, bool):
                claims_records = max(claims_records, record_count)
            verified_count = data.get("verified_count")
            if isinstance(verified_count, int) and not isinstance(verified_count, bool):
                claims_verified = max(claims_verified, verified_count)
            unsupported_count = data.get("unsupported_count")
            if isinstance(unsupported_count, int) and not isinstance(
                unsupported_count, bool
            ):
                claims_unsupported = max(claims_unsupported, unsupported_count)
    return {
        "updates": updates,
        "update_count": len(updates),
        "created_count": _count_events(events, "artifact_created"),
        "updated_count": _count_events(events, "artifact_updated"),
        "paths": _dedupe_paths([item["path"] for item in updates]),
        "report_updated": bool(report_paths),
        "report_trace_update_seen": bool(report_paths),
        "report_write_seen": _report_write_seen(events, updates),
        "report_update_count": len(report_paths),
        "report_full_write_count": _report_full_write_count(updates),
        "report_patch_count": _report_patch_count(updates),
        "report_lifecycle": _report_lifecycle_from_updates(updates),
        **_report_read_edit_flow_summary(events),
        "source_ledger_updated": bool(source_ledger_paths),
        "source_ledger_update_count": len(source_ledger_paths),
        "source_ledger_record_count": source_ledger_records,
        "claims_updated": bool(claims_paths),
        "claims_update_count": len(claims_paths),
        "claims_record_count": claims_records,
        "claims_verified_count": claims_verified,
        "claims_unsupported_count": claims_unsupported,
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
    report_trace_update_seen = bool(artifacts.get("report_trace_update_seen"))
    report_write_seen = bool(artifacts.get("report_write_seen"))
    report_full_write_count = _as_int(artifacts.get("report_full_write_count"))
    report_patch_count = _as_int(artifacts.get("report_patch_count"))
    stale_report_edit_count = _as_int(
        artifacts.get("report_targeted_edit_without_fresh_read_count")
    )
    repeated_report_read_count = _as_int(
        artifacts.get("repeated_unchanged_report_read_count")
    )
    source_ledger_updated = bool(artifacts.get("source_ledger_updated"))
    source_ledger_counts = _source_ledger_counts(events)
    assistant_chars = len(assistant_text.strip())
    long_chat_before_report_chars = _long_chat_before_first_report_update(events)
    first_report_update_before_long_chat = report_updated and (
        long_chat_before_report_chars < _DEEP_RESEARCH_LONG_CHAT_BEFORE_REPORT_CHARS
    )
    usage = llm_calls.get("usage")
    total_tokens = usage.get("total_tokens") if isinstance(usage, dict) else 0
    output_tokens = usage.get("output_tokens") if isinstance(usage, dict) else 0
    completion_after_report = _output_tokens_after_first_report_update(events)
    first_tool = tool_names[0] if tool_names else None
    missing_initial_todo = deep_expected and not _has_initial_deep_research_todo(
        tool_names
    )
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
        if tool_names.count(name) > 1
        and name not in {"web_search", *_READ_SOURCE_TOOLS}
    )
    search_diagnostics = _deep_research_search_diagnostics(
        events,
        tool_names=tool_names,
        deep_expected=deep_expected,
        research=research,
        source_ledger_counts=source_ledger_counts,
    )
    child_evidence = _child_evidence_summary(events)
    child_metrics = _child_orchestration_metrics(
        events,
        child_evidence=child_evidence,
    )
    hard_profile = _deep_research_hard_profile_summary(
        events=events,
        task_contract=task_contract,
        tool_names=tool_names,
        artifacts=artifacts,
    )
    unexpected_agent_tool = False
    skill_denied = deep_expected and _skill_tool_denied(events)
    required_verified_reads = _as_int(research.get("required_fetch_count"))
    if required_verified_reads <= 0:
        required_verified_reads = 1
    verified_read_count = source_ledger_counts["verified_reads"]
    blocked_or_failed_read_count = (
        source_ledger_counts["blocked_reads"] + source_ledger_counts["failed_reads"]
    )
    fetch_fallback_required = bool(research.get("fetch_fallback_required")) or (
        verified_read_count == 0
        and blocked_or_failed_read_count >= required_verified_reads
    )
    report_status = _deep_research_report_status(
        deep_expected=deep_expected,
        report_updated=report_updated,
        source_ledger_updated=source_ledger_updated,
        verified_read_count=verified_read_count,
        required_verified_reads=required_verified_reads,
        fetch_fallback_required=fetch_fallback_required,
    )
    source_quality = _deep_research_source_quality(
        deep_expected=deep_expected,
        report_status=report_status,
        verified_read_count=verified_read_count,
        required_verified_reads=required_verified_reads,
        candidate_count=source_ledger_counts["search_candidates"],
        blocked_read_count=source_ledger_counts["blocked_reads"],
        failed_read_count=source_ledger_counts["failed_reads"],
    )
    low_verified_coverage = report_status == "draft"
    preliminary_final = (
        low_verified_coverage
        and bool(assistant_text.strip())
        and not _assistant_declares_preliminary(assistant_text)
    )
    phase_contract = _deep_research_phase_contract_from_events(events)
    deep_research_phase = _deep_research_phase_from_contract_or_trace(
        phase_contract=phase_contract,
        deep_expected=deep_expected,
        first_tool=first_tool,
        search_call_count=search_diagnostics["search_call_count"],
        verified_read_count=verified_read_count,
        required_verified_reads=required_verified_reads,
        report_updated=report_updated,
        report_status=report_status,
    )
    phase_diagnostics = _deep_research_phase_diagnostics(
        events,
        deep_expected=deep_expected,
    )
    return {
        "deep_research_artifact_expected": deep_expected,
        "deep_research_phase": deep_research_phase,
        "deep_research_phase_next_allowed_tools": _phase_allowed_tools(phase_contract),
        **phase_diagnostics,
        "missing_report_artifact": missing_report_artifact,
        "missing_source_ledger_artifact": missing_source_ledger_artifact,
        "full_report_rewrite": full_report_rewrite,
        "stale_report_edit": stale_report_edit,
        "repeated_report_read": repeated_report_read,
        "final_references_report_artifact": final_references_report,
        "final_missing_report_reference": final_missing_report_reference,
        "missing_initial_todo": missing_initial_todo,
        "unexpected_agent_tool": unexpected_agent_tool,
        "skill_denied": skill_denied,
        "report_status": report_status,
        "report_lifecycle": _deep_research_report_lifecycle(
            report_status=report_status,
            artifacts=artifacts,
        ),
        "contract_ok": (
            not deep_expected
            or (
                report_write_seen
                and source_ledger_updated
                and bool(final_references_report)
            )
        ),
        "quality_ok": bool(source_quality["quality_ok"]),
        "quality_status": source_quality["quality_status"],
        "source_quality": source_quality,
        "verified_read_count": verified_read_count,
        "parent_search_count": tool_names.count("web_search"),
        "parent_fetch_count": _tool_count(tool_names, _READ_SOURCE_TOOLS),
        "parent_verified_read_count": verified_read_count,
        "child_search_count": child_evidence["search_count"],
        "child_fetch_count": child_evidence["fetch_count"],
        "child_verified_read_count": child_evidence["verified_read_count"],
        "child_count": child_metrics["child_count"],
        "child_tool_names": child_metrics["child_tool_names"],
        "child_summary_chars": child_metrics["child_summary_chars"],
        "duplicated_child_queries": child_metrics["duplicated_child_queries"],
        "child_source_records": child_metrics["child_source_records"],
        **hard_profile,
        "blocked_read_count": source_ledger_counts["blocked_reads"],
        "failed_read_count": source_ledger_counts["failed_reads"],
        "candidate_count": source_ledger_counts["search_candidates"],
        "required_verified_read_count": required_verified_reads,
        "low_verified_coverage": low_verified_coverage,
        "preliminary_final": preliminary_final,
        "first_tool": first_tool,
        "tool_chain": " -> ".join(tool_names),
        "unique_tool_count": len(set(tool_names)),
        "repeated_tools": repeated_tools,
        **search_diagnostics,
        "artifact_update_count": artifacts.get("update_count", 0),
        "report_trace_update_seen": report_trace_update_seen,
        "report_write_seen": report_write_seen,
        "report_update_count": artifacts.get("report_update_count", 0),
        "report_full_write_count": report_full_write_count,
        "report_patch_count": report_patch_count,
        "claims_update_count": artifacts.get("claims_update_count", 0),
        "claims_record_count": artifacts.get("claims_record_count", 0),
        "claims_verified_count": artifacts.get("claims_verified_count", 0),
        "claims_unsupported_count": artifacts.get("claims_unsupported_count", 0),
        "hard_claims_missing": bool(hard_profile["hard_requires_claims"])
        and not bool(hard_profile["hard_claims_artifact_seen"]),
        "hard_claims_empty": bool(hard_profile["hard_requires_claims"])
        and bool(hard_profile["hard_claims_artifact_seen"])
        and _as_int(artifacts.get("claims_record_count")) <= 0,
        "hard_claims_no_verified": bool(hard_profile["hard_requires_claims"])
        and bool(hard_profile["hard_claims_artifact_seen"])
        and _as_int(artifacts.get("claims_record_count")) > 0
        and _as_int(artifacts.get("claims_verified_count")) <= 0,
        "hard_claims_unsupported": bool(hard_profile["hard_requires_claims"])
        and _as_int(artifacts.get("claims_unsupported_count")) > 0,
        "report_targeted_edit_count": artifacts.get("report_targeted_edit_count", 0),
        "report_targeted_edit_without_fresh_read_count": stale_report_edit_count,
        "repeated_unchanged_report_read_count": repeated_report_read_count,
        "source_ledger_update_count": artifacts.get("source_ledger_update_count", 0),
        "source_ledger_record_count": artifacts.get("source_ledger_record_count", 0),
        "long_final_after_report": long_final_after_report,
        "assistant_chars": assistant_chars,
        "first_report_update_before_long_chat": first_report_update_before_long_chat,
        "long_chat_before_report_chars": long_chat_before_report_chars,
        "total_tokens": total_tokens if isinstance(total_tokens, int) else 0,
        "output_tokens": output_tokens if isinstance(output_tokens, int) else 0,
        "output_tokens_after_first_report_update": completion_after_report,
    }


def _deep_research_phase_diagnostics(
    events: list[dict[str, object]],
    *,
    deep_expected: bool,
) -> dict[str, Any]:
    if not deep_expected:
        return {
            "phase_violation": False,
            "phase_violation_count": 0,
            "phase_violations": [],
        }

    plan_created = False
    search_seen = False
    fetch_attempts = 0
    report_seen = False
    subagent_seen = False
    violations: list[dict[str, Any]] = []
    for event in events:
        event_name = event.get("event")
        if event_name in {"artifact_created", "artifact_updated"}:
            data = _event_data(event)
            if data.get("path") == "research/report.md":
                report_seen = True
            continue
        if event_name != "tool_call_completed":
            continue
        for tool in event_tools(_event_data(event)):
            tool_name = tool.get("tool_name") or tool.get("name")
            if not isinstance(tool_name, str) or not tool_name:
                continue
            phase = _deep_research_phase_before_tool(
                plan_created=plan_created,
                search_seen=search_seen,
                fetch_attempts=fetch_attempts,
                report_seen=report_seen,
            )
            allowed = _DEEP_RESEARCH_PHASE_ALLOWED_TOOLS.get(phase, frozenset())
            parent_synthesis_write = (
                subagent_seen
                and tool_name in {"file_write", "file_edit", "file_patch"}
                and _tool_targets_research_artifact(tool, default_when_missing=True)
            )
            if (
                tool_name not in allowed
                and tool_name not in _phase_neutral_tools()
                and not parent_synthesis_write
            ):
                violations.append(
                    {
                        "phase": phase,
                        "tool_name": tool_name,
                        "allowed_tools": sorted(allowed),
                    }
                )
            if tool_name == "todo_write":
                plan_created = True
            elif tool_name == "agent_tool":
                subagent_seen = True
            elif tool_name == "web_search":
                search_seen = True
            elif tool_name in _READ_SOURCE_TOOLS:
                fetch_attempts += 1
            elif tool_name == "file_write":
                # The artifact event is authoritative, but planned write is enough
                # for subsequent same-batch diagnostics.
                args = tool.get("args")
                if isinstance(args, dict) and _path_targets_report(args.get("path")):
                    report_seen = True
    return {
        "phase_violation": bool(violations),
        "phase_violation_count": len(violations),
        "phase_violations": violations[:10],
    }


def _tool_targets_research_artifact(
    tool: dict[str, Any], *, default_when_missing: bool = False
) -> bool:
    args = tool.get("args")
    if not isinstance(args, dict):
        return default_when_missing
    raw_path = args.get("path") or args.get("file_path")
    if not isinstance(raw_path, str):
        return default_when_missing
    path = raw_path.replace("\\", "/").strip().strip("/")
    return path in {"research/report.md", "research/sources.jsonl"} or path.endswith(
        ("/research/report.md", "/research/sources.jsonl")
    )


def _rollup_research_with_children(
    research: dict[str, Any], child_evidence: dict[str, Any]
) -> dict[str, Any]:
    """Return the research block with child evidence folded into run totals.

    Deep Research delegates discovery to children, so the run-level research
    counters must credit the children's fetches/domains — otherwise a
    delegating parent looks like it did no research even though its children
    read real pages. Parent-only values are preserved under parent_* keys so
    the split stays inspectable.
    """
    rolled = dict(research)
    parent_fetch = _as_int(research.get("fetch_count"))
    parent_attempts = _as_int(research.get("fetch_attempt_count"))
    raw_parent_domains = research.get("unique_domains")
    parent_domains = (
        list(raw_parent_domains) if isinstance(raw_parent_domains, list) else []
    )
    child_fetch = _as_int(child_evidence.get("fetch_count"))
    raw_child_domains = child_evidence.get("verified_domains")
    child_domains = (
        list(raw_child_domains) if isinstance(raw_child_domains, list) else []
    )
    merged_domains = list(parent_domains)
    for domain in child_domains:
        if domain not in merged_domains:
            merged_domains.append(domain)
    # Roll up *fetches* and *verified domains* only. search_count is the count
    # of web_search calls and gates the research search budget; child evidence
    # exposes only candidate URLs (not calls), so folding it into search_count
    # would inflate the count and trip the "search budget exhausted before
    # source diversity" stop mid-run. Keep search_count parent-only.
    rolled["parent_fetch_count"] = parent_fetch
    rolled["parent_unique_domains"] = parent_domains
    rolled["child_fetch_count"] = child_fetch
    rolled["fetch_count"] = parent_fetch + child_fetch
    rolled["fetch_attempt_count"] = parent_attempts + child_fetch
    rolled["unique_domains"] = merged_domains
    return rolled


def _child_evidence_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    totals: dict[str, Any] = {
        "search_count": 0,
        "fetch_count": 0,
        "verified_read_count": 0,
        "candidate_count": 0,
        "blocked_read_count": 0,
        "failed_read_count": 0,
    }
    domains: list[str] = []
    for event in events:
        if event.get("event") == "research_progress":
            data = _event_data(event)
            if data.get("kind") != "deep_research_child_synthesis_pending":
                continue
            evidence = data.get("child_evidence")
        elif event.get("event") == "subagent_completed":
            evidence = _event_data(event).get("child_evidence")
        else:
            continue
        if not isinstance(evidence, dict):
            continue
        for key in totals:
            value = evidence.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                totals[key] += max(0, value)
        verified_domains = evidence.get("verified_domains")
        if isinstance(verified_domains, list):
            for domain in verified_domains:
                if isinstance(domain, str) and domain and domain not in domains:
                    domains.append(domain)
    totals["verified_domains"] = domains
    return totals


def _child_orchestration_metrics(
    events: list[dict[str, object]],
    *,
    child_evidence: dict[str, int],
) -> dict[str, Any]:
    child_fingerprints: list[str] = []
    child_tool_names: set[str] = set()
    summary_chars = 0
    runs_started = 0
    runs_completed = 0
    for event in events:
        if event.get("event") not in {"subagent_started", "subagent_completed"}:
            continue
        data = _event_data(event)
        if event.get("event") == "subagent_started":
            runs_started += 1
            fingerprint = _child_task_fingerprint(data)
            if fingerprint:
                child_fingerprints.append(fingerprint)
        else:
            runs_completed += 1
        for name in _child_payload_tool_names(data):
            child_tool_names.add(name)
        raw_summary = (
            data.get("summary")
            or data.get("output_preview")
            or data.get("result_summary")
            or data.get("final_response")
        )
        if isinstance(raw_summary, str):
            summary_chars += len(raw_summary)
        raw_chars = data.get("summary_chars")
        if isinstance(raw_chars, int) and not isinstance(raw_chars, bool):
            summary_chars = max(summary_chars, raw_chars)
    duplicates = max(0, len(child_fingerprints) - len(set(child_fingerprints)))
    child_count = max(runs_started, runs_completed)
    source_records = sum(
        int(child_evidence.get(key) or 0)
        for key in (
            "verified_read_count",
            "candidate_count",
            "blocked_read_count",
            "failed_read_count",
        )
    )
    return {
        "child_count": child_count,
        "child_tool_names": sorted(child_tool_names),
        "child_summary_chars": summary_chars,
        "duplicated_child_queries": duplicates,
        "child_source_records": source_records,
    }


def _child_payload_tool_names(data: dict[str, Any]) -> list[str]:
    for key in ("used_tools", "tool_names", "tools_used"):
        value = data.get(key)
        if isinstance(value, list):
            return sorted(
                {
                    str(item).strip()
                    for item in value
                    if isinstance(item, str) and item.strip()
                }
            )
    return []


def _child_task_fingerprint(data: dict[str, Any]) -> str | None:
    for key in ("task", "description", "query", "prompt", "instructions"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return " ".join(value.lower().split())
    return None


def _deep_research_phase_before_tool(
    *,
    plan_created: bool,
    search_seen: bool,
    fetch_attempts: int,
    report_seen: bool,
) -> str:
    # Once the report artifact exists the run is in its review pass regardless of
    # who performed discovery. In the delegated (fork-join) pattern the children
    # — not the parent — call web_search/web_fetch, so without this the parent's
    # own verify+review tools (read_file/artifact_preview/file_patch + a
    # verify-fetch) would be judged against the stale "discover" phase and
    # flagged as violations.
    if report_seen:
        return "review"
    if not plan_created and not search_seen:
        return "plan"
    if not search_seen:
        return "discover"
    if fetch_attempts < _DEEP_RESEARCH_PHASE_FETCH_ATTEMPTS:
        return "verify"
    return "write"


def _phase_neutral_tools() -> frozenset[str]:
    return frozenset()


def _deep_research_phase_contract_from_events(
    events: list[dict[str, object]],
) -> dict[str, Any] | None:
    for event in reversed(events):
        data = _event_data(event)
        payload = data.get("research_session_contract")
        if isinstance(payload, dict):
            return payload
        metadata = data.get("metadata")
        if isinstance(metadata, dict):
            payload = metadata.get("research_session_contract")
            if isinstance(payload, dict):
                return payload
    return None


def _deep_research_phase_from_contract_or_trace(
    *,
    phase_contract: dict[str, Any] | None,
    deep_expected: bool,
    first_tool: str | None,
    search_call_count: int,
    verified_read_count: int,
    required_verified_reads: int,
    report_updated: bool,
    report_status: str,
) -> str:
    deep_payload = (
        phase_contract.get("deep_research")
        if isinstance(phase_contract, dict)
        else None
    )
    if isinstance(deep_payload, dict):
        phase = deep_payload.get("phase")
        if isinstance(phase, str) and phase.strip():
            return phase.strip()
    if not deep_expected:
        return "not_applicable"
    if first_tool not in {None, "todo_write"} and search_call_count == 0:
        return "plan"
    if search_call_count == 0:
        return "discover"
    if verified_read_count < required_verified_reads and report_status != "fallback":
        return "verify"
    if not report_updated:
        return "write"
    if report_status not in {"verified", "fallback"}:
        return "review"
    return "final"


def _phase_allowed_tools(phase_contract: dict[str, Any] | None) -> list[str]:
    deep_payload = (
        phase_contract.get("deep_research")
        if isinstance(phase_contract, dict)
        else None
    )
    if not isinstance(deep_payload, dict):
        return []
    tools = deep_payload.get("next_allowed_tools")
    if not isinstance(tools, list):
        return []
    return [str(item) for item in tools if isinstance(item, str) and item.strip()]


def _deep_research_search_diagnostics(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    deep_expected: bool,
    research: dict[str, Any],
    source_ledger_counts: dict[str, int],
) -> dict[str, Any]:
    search_queries = _web_search_queries(events)
    repeated_queries = sorted(
        query for query in set(search_queries) if search_queries.count(query) > 1
    )
    search_count = _as_int(research.get("search_count"))
    if search_count <= 0:
        search_count = tool_names.count("web_search")
    fetch_attempt_count = _as_int(research.get("fetch_attempt_count"))
    if fetch_attempt_count <= 0:
        fetch_attempt_count = _tool_count(tool_names, _READ_SOURCE_TOOLS)
    evidence_progress_count = (
        source_ledger_counts["verified_reads"]
        + source_ledger_counts["blocked_reads"]
        + source_ledger_counts["failed_reads"]
    )
    discovery_expansion_count = max(
        0, search_count - _DEEP_RESEARCH_INITIAL_SEARCH_BUDGET
    )
    if search_count <= _DEEP_RESEARCH_INITIAL_SEARCH_BUDGET:
        search_budget_status = "within_initial"
    elif search_count <= _DEEP_RESEARCH_HARD_SEARCH_CAP:
        search_budget_status = "expanded"
    else:
        search_budget_status = "over_hard_cap"

    repeated_search_args = deep_expected and bool(repeated_queries)
    search_without_fetch_progress = (
        deep_expected
        and search_count > _DEEP_RESEARCH_INITIAL_SEARCH_BUDGET
        and fetch_attempt_count == 0
        and evidence_progress_count == 0
    )
    tool_entropy_high = deep_expected and (
        search_count > _DEEP_RESEARCH_HARD_SEARCH_CAP
        or (len(tool_names) > 24 and evidence_progress_count == 0)
    )
    return {
        "search_call_count": search_count,
        "fetch_attempt_count": fetch_attempt_count,
        "search_initial_budget": _DEEP_RESEARCH_INITIAL_SEARCH_BUDGET,
        "search_hard_cap": _DEEP_RESEARCH_HARD_SEARCH_CAP,
        "search_budget_status": search_budget_status,
        "discovery_expansion_count": discovery_expansion_count,
        "web_search_query_count": len(search_queries),
        "repeated_search_query_count": len(repeated_queries),
        "repeated_search_queries": repeated_queries,
        "repeated_search_args": repeated_search_args,
        "search_without_fetch_progress": search_without_fetch_progress,
        "tool_entropy_high": tool_entropy_high,
    }


def _web_search_queries(events: list[dict[str, object]]) -> list[str]:
    queries: list[str] = []
    for payload in _tool_payloads(events, "web_search"):
        query = _tool_payload_string_arg(payload, "query")
        if query is None:
            query = _tool_payload_string_arg(payload, "q")
        if query is None:
            continue
        normalized = " ".join(query.lower().split())
        if normalized:
            queries.append(normalized)
    return queries


def _tool_payload_string_arg(payload: dict[str, Any], key: str) -> str | None:
    args = payload.get("args")
    if isinstance(args, dict):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value
    value = payload.get(key)
    if isinstance(value, str) and value.strip():
        return value
    return None


def _source_ledger_counts(events: list[dict[str, object]]) -> dict[str, int]:
    counts = {
        "search_candidates": 0,
        "verified_reads": 0,
        "blocked_reads": 0,
        "failed_reads": 0,
    }
    for event in events:
        if event.get("event") != "source_ledger_updated":
            continue
        data = _event_data(event)
        for key in tuple(counts):
            value = data.get(key)
            if isinstance(value, list):
                counts[key] = len(value)
    return counts


def _deep_research_report_status(
    *,
    deep_expected: bool,
    report_updated: bool,
    source_ledger_updated: bool,
    verified_read_count: int,
    required_verified_reads: int,
    fetch_fallback_required: bool,
) -> str:
    if not deep_expected:
        return "not_applicable"
    if not report_updated:
        return "missing"
    if not source_ledger_updated:
        return "invalid"
    if verified_read_count >= required_verified_reads:
        return "verified"
    if fetch_fallback_required:
        return "fallback"
    return "draft"


def _deep_research_source_quality(
    *,
    deep_expected: bool,
    report_status: str,
    verified_read_count: int,
    required_verified_reads: int,
    candidate_count: int,
    blocked_read_count: int,
    failed_read_count: int,
) -> dict[str, Any]:
    if not deep_expected:
        quality_status = "not_applicable"
    elif verified_read_count >= required_verified_reads:
        quality_status = "verified"
    elif blocked_read_count + failed_read_count >= required_verified_reads:
        quality_status = "fallback"
    elif candidate_count > 0:
        quality_status = "candidate_only"
    else:
        quality_status = (
            report_status if report_status in {"missing", "invalid"} else "draft"
        )
    return {
        "quality_ok": quality_status == "verified",
        "quality_status": quality_status,
        "required_verified_read_count": required_verified_reads,
        "verified_read_count": verified_read_count,
        "candidate_count": candidate_count,
        "blocked_read_count": blocked_read_count,
        "failed_read_count": failed_read_count,
    }


def _deep_research_hard_profile_summary(
    *,
    events: list[dict[str, object]],
    task_contract: dict[str, Any] | None,
    tool_names: list[str],
    artifacts: dict[str, Any],
) -> dict[str, Any]:
    profile = (
        str(task_contract.get("research_profile") or "")
        if isinstance(task_contract, dict)
        else ""
    )
    hard_options = (
        task_contract.get("hard_options") if isinstance(task_contract, dict) else None
    )
    allow_browser_action = (
        bool(hard_options.get("allow_browser_action"))
        if isinstance(hard_options, dict)
        else False
    )
    paths = artifacts.get("paths")
    artifact_paths = (
        {str(path) for path in paths if isinstance(path, str)}
        if isinstance(paths, list)
        else set()
    )
    source_read_count = tool_names.count("source_read")
    pdf_read_count = tool_names.count("pdf_read")
    browser_read_count = tool_names.count("browser_read")
    browser_action_count = tool_names.count("browser_action")
    source_ladder_started = source_read_count > 0 or pdf_read_count > 0
    browser_used_before_source_read = (
        profile == "hard"
        and browser_read_count > 0
        and (
            "source_read" not in tool_names
            or tool_names.index("browser_read") < tool_names.index("source_read")
        )
    )
    browser_read_missing_fallback_reason = (
        profile == "hard"
        and browser_read_count > 0
        and _browser_read_missing_fallback_reason(events)
    )
    return {
        "hard_profile": profile == "hard",
        "hard_source_ladder": {
            "source_read_count": source_read_count,
            "pdf_read_count": pdf_read_count,
            "browser_read_count": browser_read_count,
            "browser_action_count": browser_action_count,
            "source_ladder_started": source_ladder_started,
            "browser_used_before_source_read": browser_used_before_source_read,
            "browser_read_missing_fallback_reason": browser_read_missing_fallback_reason,
            "allow_browser_action": allow_browser_action,
        },
        "hard_claims_artifact_seen": bool(
            {"research/claims.jsonl", "research/claims.md"} & artifact_paths
        ),
        "hard_requires_claims": profile == "hard",
        "hard_browser_action_without_opt_in": (
            profile == "hard" and browser_action_count > 0 and not allow_browser_action
        ),
        "hard_browser_used_before_source_read": browser_used_before_source_read,
        "hard_browser_read_missing_fallback_reason": browser_read_missing_fallback_reason,
    }


def _browser_read_missing_fallback_reason(events: list[dict[str, object]]) -> bool:
    seen_browser_read = False
    for event in events:
        if event.get("event") != "tool_call_completed":
            continue
        for tool in event_tools(_event_data(event)):
            tool_name = tool.get("tool_name") or tool.get("name")
            if tool_name != "browser_read":
                continue
            seen_browser_read = True
            structured = tool.get("structured_output")
            args = tool.get("args")
            reason = None
            if isinstance(structured, dict):
                reason = structured.get("fallback_reason") or structured.get(
                    "browser_fallback_reason"
                )
            if not reason and isinstance(args, dict):
                reason = args.get("fallback_reason")
            if isinstance(reason, str) and reason.strip():
                return False
    return seen_browser_read


def _has_initial_deep_research_todo(tool_names: list[str]) -> bool:
    """Allow a successful skill lookup before the initial research todo."""
    if not tool_names:
        return True
    for name in tool_names:
        if name == "todo_write":
            return True
        if name in {"skill_tool", "skill_view"}:
            continue
        return False
    return False


def _assistant_declares_preliminary(assistant_text: str) -> bool:
    text = " ".join(assistant_text.lower().split())
    return any(
        marker in text
        for marker in (
            "preliminary",
            "draft",
            "чернов",
            "предваритель",
            "не финаль",
            "нужно ещё проверить",
            "нужно еще проверить",
        )
    )


def _skill_tool_denied(events: list[dict[str, object]]) -> bool:
    for name in ("skill_tool", "skill_view"):
        for payload in _tool_payloads(events, name):
            status = str(payload.get("status") or "").lower()
            decision = str(payload.get("decision") or "").lower()
            summary = str(payload.get("result_summary") or "").lower()
            error = str(payload.get("error") or "").lower()
            error_code = str(payload.get("error_code") or "").lower()
            if status in {"denied", "failed", "error"}:
                return True
            if decision in {"deny", "denied"}:
                return True
            if "path outside workspace" in summary or "path outside workspace" in error:
                return True
            if error_code in {"guardrail_blocked", "tool_not_registered"}:
                return True
    return False


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


def _report_patch_count(updates: list[dict[str, Any]]) -> int:
    count = 0
    for item in updates:
        if item.get("path") != "research/report.md":
            continue
        tool_name = item.get("tool_name")
        operation = item.get("operation")
        if tool_name in {"file_edit", "file_patch"} or operation in {"edit", "patch"}:
            count += 1
    return count


def _report_lifecycle_from_updates(updates: list[dict[str, Any]]) -> str:
    report_updates = [
        item for item in updates if item.get("path") == "research/report.md"
    ]
    if not report_updates:
        return "not_started"
    if any(
        item.get("operation") == "capture" or item.get("mode") == "captured_inline"
        for item in report_updates
    ):
        return "captured_inline"
    if any(
        item.get("tool_name") in {"file_edit", "file_patch"}
        or item.get("operation") in {"edit", "patch"}
        for item in report_updates
    ):
        return "patched"
    if any(item.get("tool_name") == "file_write" for item in report_updates):
        return "created"
    return "created"


def _deep_research_report_lifecycle(
    *,
    report_status: str,
    artifacts: dict[str, Any],
) -> str:
    lifecycle = str(artifacts.get("report_lifecycle") or "not_started")
    if lifecycle == "not_started":
        return lifecycle
    if report_status == "verified":
        return "ready"
    if report_status == "fallback" and lifecycle == "created":
        return "captured_inline"
    return lifecycle


def _report_write_seen(
    events: list[dict[str, object]],
    updates: list[dict[str, Any]],
) -> bool:
    for item in updates:
        if item.get("path") == "research/report.md" and item.get("tool_name") in {
            "file_write",
            "file_edit",
            "file_patch",
        }:
            return True
    return False


def _long_chat_before_first_report_update(events: list[dict[str, object]]) -> int:
    chars = 0
    for event in events:
        event_name = event.get("event")
        if event_name in {"artifact_created", "artifact_updated"}:
            data = _event_data(event)
            if data.get("path") == "research/report.md":
                break
        if event_name != "token_delta":
            continue
        data = _event_data(event)
        chunk = data.get("delta_text") or data.get("text") or data.get("content")
        if isinstance(chunk, str):
            chars += len(chunk)
    return chars


def _report_read_edit_flow_summary(events: list[dict[str, object]]) -> dict[str, int]:
    fresh_read = False
    targeted_edits = 0
    stale_targeted_edits = 0
    report_reads = 0
    repeated_unchanged_reads = 0
    report_generation = 0
    # Track which read *tools* have inspected each report generation. A single
    # multi-modal review pass (read_file + artifact_preview of the same draft
    # before patching) is legitimate, so only re-running the *same* read tool on
    # an unchanged generation counts as a redundant repeat.
    reads_by_generation: dict[int, set[str]] = {}
    for action in _report_flow_actions(events):
        if action["kind"] == "read":
            report_reads += 1
            read_tool = str(action.get("tool_name") or "read")
            seen_tools = reads_by_generation.setdefault(report_generation, set())
            if read_tool in seen_tools:
                repeated_unchanged_reads += 1
            seen_tools.add(read_tool)
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
        "deep_research_unexpected_agent_tool",
        "Deep Research used agent_tool where the active profile forbids delegation.",
    ),
    (
        "deep_research_skill_denied",
        "Deep Research hit a denied/failed skill_tool or skill_view call.",
    ),
    (
        "deep_research_low_verified_coverage",
        "Deep Research report artifact exists but source ledger coverage is still draft/pre-verified.",
    ),
    (
        "deep_research_preliminary_final",
        "Deep Research handed off a draft report as if it were final.",
    ),
    (
        "deep_research_repeated_search_args",
        "Deep Research repeated identical web_search queries instead of refining or fetching.",
    ),
    (
        "deep_research_search_without_fetch_progress",
        "Deep Research expanded search past the initial budget without fetch/ledger progress.",
    ),
    (
        "deep_research_tool_entropy_high",
        "Deep Research exceeded the hard search/tool budget without enough evidence progress.",
    ),
    (
        "deep_research_phase_violation",
        "Deep Research used a tool outside the expected phase contract.",
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
