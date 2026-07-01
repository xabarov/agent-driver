"""Deep-research efficiency, phase and search signals."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import event_data as _event_data
from agent_driver.observability.run_trace.tools import event_tools
from agent_driver.observability.run_trace.tools import tool_payloads as _tool_payloads

from ._common import (
    _DEEP_RESEARCH_HARD_SEARCH_CAP,
    _DEEP_RESEARCH_INITIAL_SEARCH_BUDGET,
    _DEEP_RESEARCH_LONG_CHAT_BEFORE_REPORT_CHARS,
    _DEEP_RESEARCH_PHASE_ALLOWED_TOOLS,
    _DEEP_RESEARCH_PHASE_FETCH_ATTEMPTS,
    _READ_SOURCE_TOOLS,
    _as_int,
    _path_targets_report,
    _tool_count,
    _tool_payload_string_arg,
)
from .subagent_signals import (
    _child_evidence_summary,
    _child_orchestration_metrics,
)


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
