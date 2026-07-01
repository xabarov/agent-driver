"""Subagent and child-evidence signals for run-trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import count_events as _count_events
from agent_driver.observability.run_trace.tools import event_data as _event_data
from agent_driver.observability.run_trace.tools import event_tools

from ._common import (
    _PARENT_SYNTHESIS_TOOLS,
    _as_int,
    _deep_research_expected_from_contract,
    _deep_research_max_subagent_requests_from_contract,
    _delegation_requested,
)
from .artifact_signals import (
    _artifact_event_is_parent_report_write,
    _artifact_event_is_source_ledger_write,
    _tool_is_parent_report_write,
)


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
