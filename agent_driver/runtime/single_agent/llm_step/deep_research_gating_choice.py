"""Deep-Research tool-surface / tool-choice gating predicates + URL-collection
helpers (extracted from request.py).

Leaf module: pure context readers (take ``context: RunContext``, no host) that
decide the allowed tool surface, the strategy tool-choice, and gather candidate
URLs for the deep-research parent. One-way (request -> deep_research_gating_choice).
"""

from __future__ import annotations
import re
from urllib.parse import urlsplit
from agent_driver.runtime.metadata_state import (
    get_tool_loop_state,
)
from agent_driver.runtime.deep_research_gating import (
    deep_research_context_enabled,
    deep_research_max_subagent_requests,
    deep_research_medium_or_hard,
    deep_research_planned_or_started_subagent_count,
    deep_research_profile,
    deep_research_tool_available,
    deep_research_tool_policy_allows,
    deep_research_tool_result_succeeded,
    is_research_report_path,
)
from agent_driver.runtime.research_artifacts import (
    deep_research_report_artifact_exists,
    deep_research_source_ledger_artifact_exists,
)
from agent_driver.runtime.research_session_contract import (
    deep_research_parent_review_pending,
    deep_research_post_artifact_next_tool,
)
from agent_driver.runtime.single_agent.types import (
    RunContext,
)

_URL_RE = re.compile(r"https?://[^\s\]\)>,;]+")


def _deep_research_request_allowed_tools(
    context: RunContext,
) -> tuple[str, ...] | None:
    """Narrow the LLM-visible tool surface during fragile synthesis states."""
    handoff = context.metadata.get("deep_research_child_synthesis")
    active = _deep_research_context_active(context) or _deep_research_initial_todo_only(
        context
    )
    if not active and not (
        isinstance(handoff, dict) and handoff.get("pending") is True
    ):
        return None
    if active:
        _record_deep_research_active_profile(context)
    if deep_research_report_artifact_exists(context):
        if deep_research_post_artifact_next_tool(context) is not None:
            # The auto-written draft created the artifacts, but the delegating
            # parent still owes tool-driven work: its own verify+review pass
            # and/or topping up the rolled-up fetch/domain floor. Keep the
            # review/verify tool surface open instead of collapsing it to
            # "artifacts ready". Filter by *policy* (not the stale effective set
            # narrowed by the prior phase) so the tools are actually re-opened.
            return tuple(
                tool_name
                for tool_name in (
                    "read_file",
                    "artifact_preview",
                    "artifact_read",
                    "artifact_list",
                    "file_patch",
                    "file_edit",
                    "web_fetch",
                    "source_read",
                    "todo_write",
                )
                if deep_research_tool_policy_allows(context, tool_name)
            )
        if deep_research_source_ledger_artifact_exists(context):
            return tuple()
        return ("file_write",)
    if deep_research_source_ledger_artifact_exists(context):
        return ("file_write",)
    deep_medium_or_hard = _deep_research_medium_or_hard_active(
        context
    ) or _deep_research_initial_todo_only(context)
    if isinstance(handoff, dict) and handoff.get("pending") is True:
        parent_synthesis_recovery = context.metadata.get(
            "deep_research_parent_synthesis_recovery"
        )
        if isinstance(parent_synthesis_recovery, dict):
            return ("file_write",)
        if _deep_research_verified_fetch_count(context) > 0:
            return ("file_write",)
        return ("file_write", "todo_write", "web_fetch")
    initial_subagent_recovery = context.metadata.get(
        "deep_research_initial_subagent_recovery"
    )
    if (
        deep_medium_or_hard
        and isinstance(initial_subagent_recovery, dict)
        and not _deep_research_tool_used(context, "agent_tool")
        and deep_research_tool_available(context, "agent_tool")
    ):
        return ("agent_tool",)
    if (
        deep_medium_or_hard
        and not _deep_research_initial_plan_seen(context)
        and not _deep_research_tool_used(context, "agent_tool")
    ):
        return tuple(
            tool_name
            for tool_name in ("todo_write",)
            if deep_research_tool_available(context, tool_name)
        )
    if (
        deep_medium_or_hard
        and _deep_research_initial_plan_seen(context)
        and not _deep_research_tool_used(context, "agent_tool")
        and deep_research_tool_available(context, "agent_tool")
    ):
        return ("agent_tool",)
    return None


def _deep_research_strategy_tool_choice(
    context: RunContext, tool_choice: object | None
) -> object | None:
    """Force high-level Deep Research profile strategy when prompts drift."""
    if tool_choice is not None:
        return tool_choice
    handoff = context.metadata.get("deep_research_child_synthesis")
    active = _deep_research_context_active(context) or _deep_research_initial_todo_only(
        context
    )
    if not active and not (
        isinstance(handoff, dict) and handoff.get("pending") is True
    ):
        return None
    if active:
        _record_deep_research_active_profile(context)
    profile = _deep_research_active_profile(context)
    if profile == "light":
        return None
    if deep_research_report_artifact_exists(context):
        next_tool = deep_research_post_artifact_next_tool(context)
        if next_tool is not None:
            if next_tool == "todo_write":
                reason = "deep_research_todo_repair_pending"
            else:
                reason = (
                    "deep_research_parent_review_pending"
                    if deep_research_parent_review_pending(context)
                    else "deep_research_discovery_floor_topup"
                )
            return _deep_research_record_strategy_choice(
                context,
                tool_name=next_tool,
                reason=reason,
                path="research/report.md" if next_tool != "web_fetch" else None,
            )
    if deep_research_report_artifact_exists(
        context
    ) and deep_research_source_ledger_artifact_exists(context):
        context.metadata["deep_research_strategy_tool_choice"] = {
            "tool": "none",
            "reason": "deep_research_artifacts_ready",
        }
        return "none"
    if deep_research_report_artifact_exists(
        context
    ) and not deep_research_source_ledger_artifact_exists(context):
        return _deep_research_record_strategy_choice(
            context,
            tool_name="file_write",
            reason="deep_research_source_ledger_missing",
            path="research/sources.jsonl",
        )
    if _deep_research_child_synthesis_pending(context):
        if _deep_research_verified_fetch_count(context) > 0:
            return _deep_research_write_strategy_tool_choice(context, force=True)
        if _deep_research_subagent_budget_remaining(context):
            return _deep_research_record_strategy_choice(
                context,
                tool_name="agent_tool",
                reason="child_synthesis_pending_with_remaining_subagent_budget",
            )
        if _deep_research_parent_verify_fetch_budget_remaining(context):
            return _deep_research_record_strategy_choice(
                context,
                tool_name="web_fetch",
                reason="child_synthesis_pending_parent_verify_fetch",
            )
        return _deep_research_write_strategy_tool_choice(context, force=True)
    if not _deep_research_initial_plan_seen(context) and deep_research_tool_available(
        context, "todo_write"
    ):
        return _deep_research_record_strategy_choice(
            context,
            tool_name="todo_write",
            reason="medium_hard_requires_initial_todo_plan",
        )
    if _deep_research_tool_used(context, "agent_tool"):
        return _deep_research_write_strategy_tool_choice(context)
    if not _deep_research_initial_plan_seen(context):
        return None
    if deep_research_max_subagent_requests(context) <= 0:
        return _deep_research_write_strategy_tool_choice(context)
    if not deep_research_tool_available(context, "agent_tool"):
        return _deep_research_write_strategy_tool_choice(context)
    return _deep_research_record_strategy_choice(
        context,
        tool_name="agent_tool",
        reason="medium_hard_requires_bounded_subagents",
    )


def _deep_research_context_active(context: RunContext) -> bool:
    if deep_research_context_enabled(context):
        return True
    return context.metadata.get("deep_research_context_active") is True


def _record_deep_research_active_profile(context: RunContext) -> None:
    context.metadata["deep_research_context_active"] = True
    profile = deep_research_profile(context)
    if profile is not None:
        context.metadata["deep_research_active_profile"] = profile


def _deep_research_active_profile(context: RunContext) -> str | None:
    profile = deep_research_profile(context)
    if profile is not None:
        return profile
    stored = context.metadata.get("deep_research_active_profile")
    return stored if isinstance(stored, str) else None


def _deep_research_medium_or_hard_active(context: RunContext) -> bool:
    if deep_research_medium_or_hard(context):
        return True
    return _deep_research_active_profile(context) in {"medium", "hard"}


def _deep_research_initial_todo_only(context: RunContext) -> bool:
    counts = _deep_research_tool_counts(context)
    if counts.get("todo_write", 0) <= 0:
        return False
    if counts.get("agent_tool", 0) > 0:
        return False
    if counts.get("web_search", 0) > 0 or counts.get("web_fetch", 0) > 0:
        return False
    return deep_research_tool_available(context, "agent_tool")


def _deep_research_write_strategy_tool_choice(
    context: RunContext,
    *,
    force: bool = False,
) -> object | None:
    if _deep_research_parent_report_write_seen(context):
        return None
    if deep_research_report_artifact_exists(context) and deep_research_tool_available(
        context, "file_patch"
    ):
        return _deep_research_record_strategy_choice(
            context,
            tool_name="file_patch",
            reason=(
                "child_synthesis_pending_budget_exhausted"
                if force
                else "deep_research_discovery_budget_reached"
            ),
            path="research/report.md",
        )
    if not deep_research_tool_available(context, "file_write"):
        return None
    if not force and not _deep_research_discovery_budget_reached(context):
        return None
    return _deep_research_record_strategy_choice(
        context,
        tool_name="file_write",
        reason=(
            "child_synthesis_pending_budget_exhausted"
            if force
            else "deep_research_discovery_budget_reached"
        ),
        path="research/report.md",
    )


def _deep_research_initial_plan_seen(context: RunContext) -> bool:
    planning_state = context.metadata.get("planning_state")
    if isinstance(planning_state, dict):
        todos = planning_state.get("todos")
        if isinstance(todos, list) and todos:
            return True
    return _deep_research_tool_used(context, "todo_write")


def _deep_research_discovery_budget_reached(context: RunContext) -> bool:
    counts = _deep_research_tool_counts(context)
    if counts.get("web_fetch", 0) >= 2:
        return True
    if counts.get("web_search", 0) >= 6:
        return True
    artifacts = context.metadata.get("deep_research_artifacts")
    if isinstance(artifacts, dict) and artifacts.get("source_ledger_exists") is True:
        return True
    return False


def _deep_research_child_synthesis_pending(context: RunContext) -> bool:
    handoff = context.metadata.get("deep_research_child_synthesis")
    return (
        isinstance(handoff, dict)
        and handoff.get("pending") is True
        and not _deep_research_parent_report_write_seen(context)
    )


def _deep_research_subagent_budget_remaining(context: RunContext) -> bool:
    return deep_research_planned_or_started_subagent_count(
        context
    ) < deep_research_max_subagent_requests(context)


def _deep_research_tool_used(context: RunContext, tool_name: str) -> bool:
    return _deep_research_tool_counts(context).get(tool_name, 0) > 0


def _deep_research_verified_fetch_count(context: RunContext) -> int:
    count = 0
    results = context.metadata.get("tool_results")
    if not isinstance(results, list):
        return 0
    for item in results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict) or call.get("tool_name") != "web_fetch":
            continue
        status = str(item.get("status") or "completed").strip().lower()
        if status in {"denied", "failed", "error", "timed_out", "timeout"}:
            continue
        count += 1
    return count


def _deep_research_parent_verify_fetch_budget_remaining(context: RunContext) -> bool:
    if not deep_research_tool_available(context, "web_fetch"):
        return False
    if _deep_research_fetch_attempt_count(context) >= 3:
        return False
    return bool(_deep_research_candidate_urls(context))


def _deep_research_candidate_urls(context: RunContext) -> set[str]:
    urls: set[str] = set()
    handoff = context.metadata.get("deep_research_child_synthesis")
    if isinstance(handoff, dict):
        _collect_urls_from_text(urls, str(handoff.get("summary") or ""))
        preview = handoff.get("child_evidence_preview")
        if isinstance(preview, list):
            for item in preview:
                if isinstance(item, dict):
                    raw = item.get("url")
                    if isinstance(raw, str) and raw.strip():
                        canonical = _canonical_url(raw)
                        if canonical is not None:
                            urls.add(canonical)
        children = handoff.get("children")
        if isinstance(children, list):
            for child in children:
                if not isinstance(child, dict):
                    continue
                _collect_urls_from_text(urls, str(child.get("summary") or ""))
                source_ledger = child.get("source_ledger")
                if isinstance(source_ledger, dict):
                    _collect_urls_from_source_ledger(urls, source_ledger)
        source_ledger = handoff.get("source_ledger")
        if isinstance(source_ledger, dict):
            _collect_urls_from_source_ledger(urls, source_ledger)
    return {url for url in urls if url}


def _collect_urls_from_source_ledger(
    urls: set[str], source_ledger: dict[str, object]
) -> None:
    for section in (
        "search_candidates",
        "verified_reads",
        "blocked_reads",
        "failed_reads",
    ):
        rows = source_ledger.get(section)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            canonical = _canonical_url(row.get("url") or row.get("canonical_url"))
            if canonical is not None:
                urls.add(canonical)


def _collect_urls_from_text(urls: set[str], text: str) -> None:
    for match in _URL_RE.finditer(text):
        canonical = _canonical_url(match.group(0).rstrip(".,;:"))
        if canonical is not None:
            urls.add(canonical)


def _canonical_url(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = urlsplit(value.strip())
    except ValueError:
        return None
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    hostname = parsed.hostname
    if not hostname:
        return None
    netloc = hostname.lower()
    if parsed.port is not None:
        netloc = f"{netloc}:{parsed.port}"
    path = parsed.path.rstrip("/")
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme.lower()}://{netloc}{path}{query}"


def _deep_research_parent_search_fallback_required(context: RunContext) -> bool:
    if not deep_research_tool_available(context, "web_search"):
        return False
    if _deep_research_tool_counts(context).get("web_search", 0) > 0:
        return False
    if _deep_research_fetch_attempt_count(context) > 0:
        return False
    return True


def _deep_research_fetch_attempt_count(context: RunContext) -> int:
    count = 0
    results = context.metadata.get("tool_results")
    if not isinstance(results, list):
        return 0
    for item in results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if isinstance(call, dict) and call.get("tool_name") == "web_fetch":
            count += 1
    return count


def _deep_research_tool_counts(context: RunContext) -> dict[str, int]:
    counts: dict[str, int] = {}
    results = context.metadata.get("tool_results")
    if not isinstance(results, list):
        return counts
    for item in results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        name = call.get("tool_name")
        if not isinstance(name, str) or not name:
            continue
        counts[name] = counts.get(name, 0) + 1
    return counts


def _deep_research_parent_report_write_seen(context: RunContext) -> bool:
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        if not deep_research_tool_result_succeeded(item):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        if call.get("tool_name") not in {"file_write", "file_patch", "file_edit"}:
            continue
        args = call.get("args")
        if not isinstance(args, dict):
            continue
        if is_research_report_path(args.get("path") or args.get("file_path")):
            return _report_artifact_confirmed_if_possible(context)
    return False


def _report_artifact_confirmed_if_possible(context: RunContext) -> bool:
    if "workspace_cwd" in context.metadata or isinstance(
        context.metadata.get("deep_research_artifacts"), dict
    ):
        return deep_research_report_artifact_exists(context)
    return True


def _deep_research_record_strategy_choice(
    context: RunContext,
    *,
    tool_name: str,
    reason: str,
    path: str | None = None,
) -> dict[str, str]:
    choice = {"type": "tool", "name": tool_name}
    payload = {"tool": tool_name, "reason": reason}
    if path is not None:
        payload["path"] = path
    context.metadata["deep_research_strategy_tool_choice"] = payload
    return choice
