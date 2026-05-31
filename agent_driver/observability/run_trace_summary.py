"""Summarize one chat run into scenario-checkable quality signals."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from agent_driver.observability.run_trace_compaction import (
    compaction_summary as _compaction_summary,
    context_pressure_summary as _context_pressure_summary,
)
from agent_driver.observability.run_trace_provider import (
    llm_call_summary as _llm_call_summary,
    prompt_surface_summary as _prompt_surface_summary,
    provider_profile_summary as _provider_profile_summary,
    provider_rejected as _provider_rejected,
)
from agent_driver.observability.run_trace_tools import (
    assistant_text as _assistant_text,
    count_events as _count_events,
    event_data as _event_data,
    interrupt_reasons as _interrupt_reasons,
    tool_names as _tool_names,
    tool_payloads as _tool_payloads,
    unknown_tool_summary as _unknown_tool_summary,
)
from agent_driver.runtime.planning_check import (
    EXIT_PLAN_MODE_TOOL_NAMES,
    PLANNING_TOOL_NAMES,
)
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    classify_research_depth,
)
from agent_driver.runtime.research_session_contract import unfinished_todo_labels
from agent_driver.runtime.single_agent.continuation import analyze_continuation_intent

_RESEARCH_TOOLS = frozenset({"web_search", "web_fetch"})
_PYTHON_TOOL = "python"
_TERMINAL_EVENTS = frozenset({"run_completed", "run_failed", "run_cancelled"})
_FETCH_REQUIRED_MARKERS = (
    "открой",
    "открыть",
    "загрузи",
    "прочитай url",
    "web_fetch",
    "fetch",
    "open url",
    "open the url",
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


def _requires_research(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> bool:
    text = " ".join((user_prompt or "").lower().split())
    if any(
        marker in text
        for marker in (
            "без поиска",
            "без интернета",
            "не ищи",
            "не используй интернет",
            "по памяти",
            "no search",
            "without search",
            "without web",
            "do not search",
        )
    ):
        return False
    if (
        isinstance(task_contract, dict)
        and task_contract.get("requires_research") is True
    ):
        return True
    if _is_plan_only_prompt(text):
        return False
    return any(
        marker in text
        for marker in (
            "найди",
            "поиск",
            "интернет",
            "источник",
            "research",
            "search",
            "source",
        )
    )


def _research_summary(
    events: list[dict[str, object]],
    *,
    tool_names: list[str],
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
    task_contract: dict[str, Any] | None,
    planning: dict[str, Any],
) -> dict[str, Any]:
    depth = _research_depth(
        task_contract=task_contract,
        user_prompt=user_prompt,
        requires_research=requires_research,
    )
    search_count = tool_names.count("web_search")
    fetch_payloads = [
        payload
        for payload in _tool_payloads(events, "web_fetch")
        if _tool_payload_succeeded(payload)
    ]
    research_payloads = [
        payload
        for tool_name in _RESEARCH_TOOLS
        for payload in _tool_payloads(events, tool_name)
        if _tool_payload_succeeded(payload)
    ]
    fetch_count = len(fetch_payloads)
    domains = _unique_domains(fetch_payloads)
    final_has_source_links = _has_source_links(assistant_text) or _has_tool_sources(
        research_payloads
    )
    fetch_required = _fetch_required(
        task_contract=task_contract,
        user_prompt=user_prompt,
    )
    fetch_required_but_missing = (
        (depth == RESEARCH_DEPTH_SOURCE_VERIFIED or fetch_required)
        and search_count > 0
        and fetch_count < (SOURCE_VERIFIED_FETCHES if not fetch_required else 1)
    )
    insufficient_source_diversity = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and fetch_count >= SOURCE_VERIFIED_FETCHES
        and len(domains) < SOURCE_VERIFIED_DOMAINS
    )
    final_missing_source_links = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and fetch_count >= SOURCE_VERIFIED_FETCHES
        and len(domains) >= SOURCE_VERIFIED_DOMAINS
        and not final_has_source_links
    )
    repair_reasons: list[str] = []
    if _planning_todos_incomplete(
        planning,
        assistant_text=assistant_text,
        allow_all_todos=_research_final_metrics_cover_plan_todos(
            requires_research=requires_research,
            search_count=search_count,
            fetch_required_but_missing=fetch_required_but_missing,
            insufficient_source_diversity=insufficient_source_diversity,
            final_missing_source_links=final_missing_source_links,
            assistant_text=assistant_text,
        ),
    ):
        repair_reasons.append("unfinished_todos")
    if requires_research and search_count == 0 and fetch_count == 0:
        repair_reasons.append("missing_research_evidence")
    if fetch_required_but_missing:
        repair_reasons.append("missing_fetched_sources")
    if insufficient_source_diversity:
        repair_reasons.append("insufficient_source_diversity")
    if final_missing_source_links:
        repair_reasons.append("final_missing_source_links")
    final_readiness = "repair_needed" if repair_reasons else "allowed"
    return {
        "depth": depth,
        "search_count": search_count,
        "fetch_count": fetch_count,
        "required_fetch_count": (
            SOURCE_VERIFIED_FETCHES
            if depth == RESEARCH_DEPTH_SOURCE_VERIFIED
            else (1 if requires_research or fetch_required else 0)
        ),
        "fetch_required": fetch_required,
        "fetch_required_but_missing": fetch_required_but_missing,
        "insufficient_source_diversity": insufficient_source_diversity,
        "unique_domains": domains,
        "final_has_source_links": final_has_source_links,
        "final_missing_source_links": final_missing_source_links,
        "final_readiness": final_readiness,
        "repair_required_reasons": repair_reasons,
    }


def _research_depth(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
    requires_research: bool,
) -> str:
    if isinstance(task_contract, dict):
        depth = task_contract.get("research_depth")
        if isinstance(depth, str) and depth:
            return depth
    return classify_research_depth(
        user_prompt or "",
        requires_research=requires_research,
        plan_only=_is_plan_only_prompt(" ".join((user_prompt or "").lower().split())),
    )


def _fetch_required(
    *,
    task_contract: dict[str, Any] | None,
    user_prompt: str | None,
) -> bool:
    if isinstance(task_contract, dict) and task_contract.get("fetch_required") is True:
        return True
    text = " ".join((user_prompt or "").lower().split())
    return any(marker in text for marker in _FETCH_REQUIRED_MARKERS)


def _unique_domains(payloads: list[dict[str, Any]]) -> list[str]:
    domains: list[str] = []
    for payload in payloads:
        url = _payload_url(payload)
        if not url:
            continue
        domain = urlparse(url).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        if domain and domain not in domains:
            domains.append(domain)
    return domains


def _payload_url(payload: dict[str, Any]) -> str | None:
    args = payload.get("args")
    if isinstance(args, dict):
        url = args.get("url")
        if isinstance(url, str) and url:
            return url
    url = payload.get("url")
    if isinstance(url, str) and url:
        return url
    return None


def _tool_payload_succeeded(payload: dict[str, Any]) -> bool:
    status = str(payload.get("status") or "").lower()
    if status in {"failed", "error", "denied", "timed_out", "timeout"}:
        return False
    status_code = payload.get("status_code")
    if isinstance(status_code, int) and not isinstance(status_code, bool):
        return status_code < 400
    if payload.get("error_code") or payload.get("error"):
        return False
    summary = str(payload.get("result_summary") or "").lower()
    if re.search(r"\bhttp\s+[45]\d\d\b", summary):
        return False
    if any(
        marker in summary
        for marker in ("failed:", "blocked by upstream", "unsupported content type")
    ):
        return False
    return True


def _has_tool_sources(payloads: list[dict[str, Any]]) -> bool:
    return any(
        isinstance(payload.get("sources"), list) and payload["sources"]
        for payload in payloads
    )


def _has_source_links(text: str) -> bool:
    return bool(re.search(r"https?://|\[[^\]]+\]\(https?://", text))


def _research_final_answer_covers_plan_todos(
    *,
    requires_research: bool,
    research: dict[str, Any],
    assistant_text: str,
) -> bool:
    return _research_final_metrics_cover_plan_todos(
        requires_research=requires_research,
        search_count=int(research.get("search_count") or 0),
        fetch_required_but_missing=bool(research.get("fetch_required_but_missing")),
        insufficient_source_diversity=bool(
            research.get("insufficient_source_diversity")
        ),
        final_missing_source_links=bool(research.get("final_missing_source_links")),
        assistant_text=assistant_text,
    )


def _research_final_metrics_cover_plan_todos(
    *,
    requires_research: bool,
    search_count: int,
    fetch_required_but_missing: bool,
    insufficient_source_diversity: bool,
    final_missing_source_links: bool,
    assistant_text: str,
) -> bool:
    if not requires_research:
        return False
    if search_count <= 0:
        return False
    if len(assistant_text.strip()) < 200:
        return False
    return not (
        fetch_required_but_missing
        or insufficient_source_diversity
        or final_missing_source_links
    )


def _is_plan_only_prompt(text: str) -> bool:
    return any(
        marker in text
        for marker in (
            "только план",
            "только план поиска",
            "без реферата",
            "без черновика",
            "plan only",
            "only plan",
            "just the plan",
            "no report",
            "without writing",
        )
    )


def _planning_summary(
    events: list[dict[str, object]],
    tool_names: list[str],
) -> dict[str, Any]:
    planning_tool_count = sum(1 for name in tool_names if name in PLANNING_TOOL_NAMES)
    enter_plan_count = tool_names.count("enter_plan_mode")
    exit_plan_count = sum(1 for name in tool_names if name in EXIT_PLAN_MODE_TOOL_NAMES)
    data_tool_count = sum(1 for name in tool_names if name not in PLANNING_TOOL_NAMES)
    snapshots = 0
    latest_snapshot: dict[str, Any] | None = None
    for event in events:
        snapshot = _event_data(event).get("planning_snapshot")
        if isinstance(snapshot, dict):
            snapshots += 1
            latest_snapshot = dict(snapshot)
    if planning_tool_count == 0:
        verdict = None
    else:
        verdict = "engaged" if data_tool_count > 0 else "fabricated"
    return {
        "verdict": verdict,
        "planning_tool_calls": planning_tool_count,
        "approval_cycles": min(enter_plan_count, exit_plan_count),
        "enter_plan_mode_calls": enter_plan_count,
        "exit_plan_mode_calls": exit_plan_count,
        "data_tool_calls": data_tool_count,
        "snapshots": snapshots,
        "latest_snapshot": latest_snapshot,
    }


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


def _planning_todos_incomplete(
    planning: dict[str, Any],
    *,
    assistant_text: str = "",
    allow_all_todos: bool = False,
) -> bool:
    if allow_all_todos:
        return False
    latest = planning.get("latest_snapshot")
    if not isinstance(latest, dict):
        return False
    todos = latest.get("todos")
    if not isinstance(todos, list):
        return False
    normalized_todos: list[dict[str, Any]] = []
    for todo in todos:
        if not isinstance(todo, dict):
            continue
        item = dict(todo)
        if "todo_id" not in item and "id" in item:
            item["todo_id"] = item.pop("id")
        normalized_todos.append(item)
    planning_state = {
        "run_id": "trace_summary",
        "todos": normalized_todos,
        "metadata": {},
    }
    return bool(unfinished_todo_labels(planning_state, assistant_text=assistant_text))


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


def _planning_execution_expected(
    *,
    requires_research: bool,
    user_prompt: str | None,
    assistant_text: str,
) -> bool:
    if requires_research:
        return True
    prompt = " ".join((user_prompt or "").lower().split())
    if any(marker in prompt for marker in ("выполни", "execute", "implement", "fix")):
        return True
    answer = assistant_text.lower()
    return any(
        marker in answer
        for marker in (
            "данные собраны",
            "источники изучены",
            "были выполнены",
            "проведён поиск",
            "проведен поиск",
            "research completed",
            "data collected",
        )
    )


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
