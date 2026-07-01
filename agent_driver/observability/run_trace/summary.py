"""Summarize one chat run into scenario-checkable quality signals."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.compaction import (
    compaction_summary as _compaction_summary,
)
from agent_driver.observability.run_trace.compaction import (
    context_pressure_summary as _context_pressure_summary,
)
from agent_driver.observability.run_trace.planning import (
    planning_execution_expected as _planning_execution_expected,
)
from agent_driver.observability.run_trace.planning import (
    planning_summary as _planning_summary,
)
from agent_driver.observability.run_trace.planning import (
    planning_todos_incomplete as _planning_todos_incomplete,
)
from agent_driver.observability.run_trace.provider import (
    llm_call_summary as _llm_call_summary,
)
from agent_driver.observability.run_trace.provider import (
    prompt_surface_summary as _prompt_surface_summary,
)
from agent_driver.observability.run_trace.provider import (
    provider_profile_summary as _provider_profile_summary,
)
from agent_driver.observability.run_trace.provider import (
    provider_rejected as _provider_rejected,
)
from agent_driver.observability.run_trace.research import (
    RESEARCH_TOOLS as _RESEARCH_TOOLS,
)
from agent_driver.observability.run_trace.research import (
    requires_research as _requires_research,
)
from agent_driver.observability.run_trace.research import (
    research_final_answer_covers_plan_todos as _research_final_answer_covers_plan_todos,
)
from agent_driver.observability.run_trace.research import (
    research_summary as _research_summary,
)
from agent_driver.observability.run_trace.tools import assistant_text as _assistant_text
from agent_driver.observability.run_trace.tools import count_events as _count_events
from agent_driver.observability.run_trace.tools import event_data as _event_data
from agent_driver.observability.run_trace.tools import (
    interrupt_reasons as _interrupt_reasons,
)
from agent_driver.observability.run_trace.tools import tool_names as _tool_names
from agent_driver.observability.run_trace.tools import (
    unknown_tool_summary as _unknown_tool_summary,
)
from agent_driver.runtime.single_agent.lifecycle.continuation import (
    analyze_continuation_intent,
)

from ._common import (
    _TERMINAL_EVENTS,
    _as_int,
    _deep_research_expected_from_contract,
    _deep_research_max_subagent_requests_from_contract,
    _last_event_name,
    _simple_prompt,
)
from .artifact_signals import _artifact_summary
from .python_signals import _python_summary
from .research_signals import (
    _agent_tool_prompt_unbounded,
    _research_efficiency_summary,
)
from .subagent_signals import (
    _child_evidence_summary,
    _rollup_research_with_children,
    _subagent_summary,
)


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
