"""Shared Deep Research gating helpers.

The runtime steers Deep Research in multiple layers: request schema narrowing,
strategy tool choice, execution gates, and tool-stage repair. Keep the core
contract/profile/path predicates here so those layers do not drift apart.
"""

from __future__ import annotations

from typing import Any

from agent_driver.runtime.metadata_state import get_tool_loop_state


def deep_research_contract_expected(task_contract: dict[str, Any]) -> bool:
    """Return True when a task contract intentionally selects Deep Research."""
    depth = task_contract.get("research_depth")
    profile = str(task_contract.get("research_profile") or "").strip().lower()
    return (
        task_contract.get("research_mode") == "deep"
        or depth == "deep_parallel_research"
        or (depth == "source_verified_report" and profile in {"medium", "hard"})
    )


def deep_research_context_enabled(context: Any) -> bool:
    """Return True when context metadata indicates Deep Research behavior."""
    metadata = context.run_input.tool_policy.metadata
    task_contract = metadata.get("task_contract")
    if isinstance(task_contract, dict) and deep_research_contract_expected(
        task_contract
    ):
        return True
    mode = metadata.get("deep_research_mode")
    if isinstance(mode, dict) and mode.get("enabled") is True:
        return True
    app_metadata = context.run_input.app_metadata
    return (
        app_metadata.get("research_mode") == "deep"
        or app_metadata.get("research_depth") == "deep_parallel_research"
    )


def deep_research_profile(context: Any, default: str = "medium") -> str:
    """Return normalized Deep Research profile from mode metadata or contract."""
    metadata = context.run_input.tool_policy.metadata
    mode = metadata.get("deep_research_mode")
    if isinstance(mode, dict):
        value = mode.get("research_profile")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    task_contract = metadata.get("task_contract")
    if isinstance(task_contract, dict):
        value = task_contract.get("research_profile")
        if isinstance(value, str) and value.strip():
            return value.strip().lower()
    app_metadata = context.run_input.app_metadata
    value = app_metadata.get("research_profile")
    if isinstance(value, str) and value.strip():
        return value.strip().lower()
    return default


def deep_research_medium_or_hard(context: Any) -> bool:
    """Return True for enabled Deep Research profiles that require gating."""
    if not deep_research_context_enabled(context):
        return False
    return deep_research_profile(context) != "light"


def deep_research_max_subagent_requests(context: Any) -> int:
    """Return bounded child request count for the active Deep Research profile."""
    metadata = context.run_input.tool_policy.metadata
    task_contract = metadata.get("task_contract")
    if isinstance(task_contract, dict):
        raw = task_contract.get("max_subagent_requests")
        if isinstance(raw, int) and not isinstance(raw, bool):
            return max(0, raw)
    profile = deep_research_profile(context)
    if profile == "light":
        return 0
    if profile == "hard":
        return 4
    return 1


def deep_research_planned_or_started_subagent_count(context: Any) -> int:
    """Count child tasks already planned or started for this run."""
    count = 0
    planned = context.metadata.get("planned_subagent_group")
    if isinstance(planned, dict) and isinstance(planned.get("tasks"), list):
        count += len([item for item in planned["tasks"] if isinstance(item, dict)])
    runs = context.metadata.get("subagent_runs")
    if isinstance(runs, list):
        count += len([item for item in runs if isinstance(item, dict)])
    return count


def deep_research_tool_available(context: Any, tool_name: str) -> bool:
    """Return True when policy and effective runtime tools allow a tool."""
    if not deep_research_tool_policy_allows(context, tool_name):
        return False
    effective = get_tool_loop_state(context).effective_tool_names()
    if effective is not None and tool_name not in set(effective):
        return False
    return True


def deep_research_tool_policy_allows(context: Any, tool_name: str) -> bool:
    """Return True when the static tool policy permits a tool.

    Unlike ``deep_research_tool_available`` this ignores the *effective* tool
    set, which is itself narrowed per-request. When building a fresh
    ``request_allowed_tools`` surface (e.g. opening the parent review/verify
    tools) we must consult the policy, not the previous turn's narrowed set —
    otherwise the new surface is filtered down to whatever the prior phase
    already allowed, defeating the widening.
    """
    policy = context.run_input.tool_policy
    if tool_name in set(policy.denied_tools or []):
        return False
    allowed = policy.allowed_tools
    if allowed is not None and tool_name not in set(allowed):
        return False
    return True


def normalize_artifact_path(value: object) -> str:
    """Normalize tool artifact paths for report/source-ledger comparisons."""
    if not isinstance(value, str):
        return ""
    return value.replace("\\", "/").strip().strip("/")


def is_research_report_path(value: object) -> bool:
    path = normalize_artifact_path(value)
    return path == "research/report.md" or path.endswith("/research/report.md")


def is_research_source_ledger_path(value: object) -> bool:
    path = normalize_artifact_path(value)
    return path == "research/sources.jsonl" or path.endswith("/research/sources.jsonl")


def deep_research_tool_result_succeeded(item: dict[str, Any]) -> bool:
    """Return False for failed, denied, interrupted, or cancelled tool results."""
    if item.get("error"):
        return False
    decision = str(item.get("decision") or "").strip().lower()
    if decision in {"deny", "denied", "interrupt", "rejected"}:
        return False
    status = str(item.get("status") or "").strip().lower()
    if status in {"denied", "failed", "error", "timed_out", "timeout", "cancelled"}:
        return False
    return True
