"""Shared low-level primitives for run-trace signal extraction."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.research import (
    requires_research as _requires_research,
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
