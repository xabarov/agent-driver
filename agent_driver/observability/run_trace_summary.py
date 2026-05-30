"""Summarize one chat run into scenario-checkable quality signals."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from agent_driver.runtime.chat_policy import is_python_reliability_request
from agent_driver.runtime.planning_check import PLANNING_TOOL_NAMES
from agent_driver.runtime.research_evidence import (
    RESEARCH_DEPTH_SOURCE_VERIFIED,
    SOURCE_VERIFIED_DOMAINS,
    SOURCE_VERIFIED_FETCHES,
    classify_research_depth,
)
from agent_driver.runtime.single_agent.continuation import analyze_continuation_intent

_RESEARCH_TOOLS = frozenset({"web_search", "web_fetch"})
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
    research = _research_summary(
        events,
        tool_names=tool_names,
        requires_research=requires_research,
        user_prompt=user_prompt,
        assistant_text=text,
        task_contract=task_contract,
    )
    planning = _planning_summary(events, tool_names)
    llm_calls = _llm_call_summary(events)
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
    provider_rejected = _provider_rejected(events)
    unknown_tools = _unknown_tool_summary(events)

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
            terminal_event == "run_completed" and _planning_todos_incomplete(planning)
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
        "missed_python": (python["python_expected"] and not python["python_tool_used"]),
        "python_no_final": (
            python["python_tool_used"] and not python["final_after_python"]
        ),
        "python_policy_loop": python["python_policy_errors"] > 1,
        "unnecessary_python": (
            python["python_tool_used"]
            and not python["python_expected"]
            and _simple_prompt(user_prompt)
        ),
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
        "unknown_tools": unknown_tools,
        "provider_rejected": provider_rejected,
        "interrupts": interrupt_reasons,
        "continuation_reason": continuation.reason,
        "failures": failures,
        "notes": notes,
    }


def _count_events(events: list[dict[str, object]], event_name: str) -> int:
    return sum(1 for event in events if event.get("event") == event_name)


def _provider_rejected(events: list[dict[str, object]]) -> bool:
    return any(event.get("event") == "llm_request_rejected" for event in events)


def _compaction_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    compaction_events = [
        event
        for event in events
        if event.get("event") in {"memory_compaction_started", "memory_compacted"}
    ]
    started = [
        event
        for event in compaction_events
        if event.get("event") == "memory_compaction_started"
    ]
    outcomes = [
        _event_data(event)
        for event in compaction_events
        if event.get("event") == "memory_compacted"
    ]
    outcome_counts = {
        "successful": sum(
            1 for data in outcomes if data.get("outcome") == "successful"
        ),
        "failed": sum(1 for data in outcomes if data.get("outcome") == "failed"),
        "skipped": sum(1 for data in outcomes if data.get("outcome") == "skipped"),
    }
    modes: list[str] = []
    for event in compaction_events:
        mode = _event_data(event).get("mode")
        if isinstance(mode, str) and mode and mode not in modes:
            modes.append(mode)
    latest_data = _event_data(compaction_events[-1]) if compaction_events else {}
    latest_state = latest_data.get("compaction_state")
    latest = None
    if compaction_events:
        latest = {
            "event": compaction_events[-1].get("event"),
            "outcome": latest_data.get("outcome"),
            "mode": latest_data.get("mode"),
            "compaction_id": latest_data.get("compaction_id"),
            "failure_kind": latest_data.get("failure_kind"),
            "summarized_message_count": latest_data.get("summarized_message_count"),
        }
    return {
        "attempts": max(
            len(started), outcome_counts["successful"] + outcome_counts["failed"]
        ),
        "started": len(started),
        **outcome_counts,
        "modes": modes,
        "circuit_breaker_open": (
            latest_state.get("circuit_breaker_open")
            if isinstance(latest_state, dict)
            else False
        ),
        "latest": latest,
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


def _event_data(event: dict[str, object]) -> dict[str, Any]:
    data = event.get("data")
    return data if isinstance(data, dict) else {}


def _event_tools(data: dict[str, Any]) -> list[dict[str, Any]]:
    tools = data.get("tools")
    if isinstance(tools, list):
        return [tool for tool in tools if isinstance(tool, dict)]
    direct = data.get("tool_name")
    if isinstance(direct, str) and direct:
        return [data]
    return []


def _tool_names(events: list[dict[str, object]]) -> list[str]:
    names: list[str] = []
    for event in events:
        name = event.get("event")
        if name not in {"tool_call_started", "tool_call_completed"}:
            continue
        data = _event_data(event)
        direct = data.get("tool_name")
        if isinstance(direct, str) and direct:
            names.append(direct)
        tools = data.get("tools")
        if not isinstance(tools, list):
            continue
        for tool in tools:
            if not isinstance(tool, dict):
                continue
            tool_name = tool.get("tool_name") or tool.get("name")
            if isinstance(tool_name, str) and tool_name:
                names.append(tool_name)
    return names


def _tool_payloads(
    events: list[dict[str, object]],
    tool_name: str,
) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") not in {"tool_call_started", "tool_call_completed"}:
            continue
        data = _event_data(event)
        if data.get("tool_name") == tool_name:
            payloads.append(data)
        tools = data.get("tools")
        if isinstance(tools, list):
            payloads.extend(
                tool
                for tool in tools
                if isinstance(tool, dict)
                and (
                    tool.get("tool_name") == tool_name or tool.get("name") == tool_name
                )
            )
    return payloads


def _interrupt_reasons(events: list[dict[str, object]]) -> list[str]:
    reasons: list[str] = []
    for event in events:
        if event.get("event") not in {"interrupt_requested", "run_paused"}:
            continue
        data = _event_data(event)
        reason = data.get("reason")
        if isinstance(reason, str) and reason:
            reasons.append(reason)
        elif event.get("event") == "run_paused":
            reasons.append("run_paused")
    return reasons


def _assistant_text(events: list[dict[str, object]]) -> str:
    chunks: list[str] = []
    for event in events:
        if event.get("event") != "token_delta":
            continue
        data = _event_data(event)
        chunk = data.get("delta_text") or data.get("text") or data.get("content")
        if isinstance(chunk, str):
            chunks.append(chunk)
    return "".join(chunks)


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
    fetch_count = len(fetch_payloads)
    domains = _unique_domains(fetch_payloads)
    final_has_source_links = _has_source_links(assistant_text)
    fetch_required_but_missing = (
        depth == RESEARCH_DEPTH_SOURCE_VERIFIED
        and search_count > 0
        and fetch_count < SOURCE_VERIFIED_FETCHES
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
    return {
        "depth": depth,
        "search_count": search_count,
        "fetch_count": fetch_count,
        "required_fetch_count": (
            SOURCE_VERIFIED_FETCHES
            if depth == RESEARCH_DEPTH_SOURCE_VERIFIED
            else (1 if requires_research else 0)
        ),
        "fetch_required_but_missing": fetch_required_but_missing,
        "insufficient_source_diversity": insufficient_source_diversity,
        "unique_domains": domains,
        "final_has_source_links": final_has_source_links,
        "final_missing_source_links": final_missing_source_links,
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


def _has_source_links(text: str) -> bool:
    return bool(re.search(r"https?://|\[[^\]]+\]\(https?://", text))


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
    exit_plan_count = tool_names.count("exit_plan_mode_v2")
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


def _llm_call_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    tool_choices: list[Any] = []
    force_final_reasons: list[str] = []
    continuation_reasons: list[str] = []
    for event in events:
        if event.get("event") != "llm_call_started":
            continue
        data = _event_data(event)
        if "tool_choice_effective" in data:
            tool_choices.append(data.get("tool_choice_effective"))
        force_final_reason = data.get("force_final_reason")
        if isinstance(force_final_reason, str) and force_final_reason:
            force_final_reasons.append(force_final_reason)
        continuation_reason = data.get("continuation_reason")
        if isinstance(continuation_reason, str) and continuation_reason:
            continuation_reasons.append(continuation_reason)
    return {
        "started": _count_events(events, "llm_call_started"),
        "completed": _count_events(events, "llm_call_completed"),
        "tool_choice_effective": tool_choices,
        "force_final_reasons": force_final_reasons,
        "continuation_reasons": continuation_reasons,
    }


def _prompt_surface_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    effective_tool_names: list[str] = []
    prompt_fragments: list[str] = []
    for event in events:
        if event.get("event") != "llm_call_completed":
            continue
        data = _event_data(event)
        tools = data.get("effective_tool_names")
        if isinstance(tools, list):
            effective_tool_names.extend(
                item for item in tools if isinstance(item, str) and item
            )
        fragments = data.get("prompt_fragments")
        if isinstance(fragments, list):
            prompt_fragments.extend(
                item for item in fragments if isinstance(item, str) and item
            )
    return {
        "effective_tool_names": _dedupe_preserve_order(effective_tool_names),
        "prompt_fragments": _dedupe_preserve_order(prompt_fragments),
    }


def _unknown_tool_summary(events: list[dict[str, object]]) -> dict[str, Any]:
    names: list[str] = []
    suggestions: list[str] = []
    for event in events:
        if event.get("event") != "tool_call_completed":
            continue
        data = _event_data(event)
        for tool in _event_tools(data):
            if str(tool.get("error_code") or "") != "tool_not_registered":
                continue
            name = tool.get("tool_name")
            if isinstance(name, str) and name:
                names.append(name)
            summary = tool.get("result_summary")
            if isinstance(summary, str) and summary:
                suggestions.append(summary)
    return {
        "count": len(names),
        "names": _dedupe_preserve_order(names),
        "suggestions": suggestions[:3],
    }


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


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


def _planning_todos_incomplete(planning: dict[str, Any]) -> bool:
    latest = planning.get("latest_snapshot")
    if not isinstance(latest, dict):
        return False
    completed = latest.get("completed")
    total = latest.get("total")
    if not isinstance(completed, int) or not isinstance(total, int):
        return False
    return total > 0 and completed < total


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
        "python_tool_available": python_tool_used or _python_expected(user_prompt),
        "python_tool_used": python_tool_used,
        "python_calls": tool_names.count(_PYTHON_TOOL),
        "python_policy_errors": sum(
            1
            for text in result_texts
            if "python policy:" in text.lower() or "unauthorized import" in text.lower()
        ),
        "python_timeouts": sum(1 for text in result_texts if "timeout" in text.lower()),
        "python_expected": _python_expected(user_prompt),
        "missed_python_for_calculation": (
            _python_expected(user_prompt) and not python_tool_used
        ),
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


def _python_expected(user_prompt: str | None) -> bool:
    return is_python_reliability_request(user_prompt or "")


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
