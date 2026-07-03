"""Diagnostics-only tool-loop guardrail detectors for trace summaries."""

from __future__ import annotations

import json
from typing import Any

from agent_driver.observability.run_trace.tools import event_data, event_tools

_READ_LIKE_TOOLS = frozenset(
    {
        "web_search",
        "web_fetch",
        "source_read",
        "browser_read",
        "read_file",
        "file_read",
        "grep_search",
        "glob_search",
        "list_dir",
    }
)


def diagnostic_tool_guardrail_decisions(
    events: list[dict[str, object]],
    *,
    run_id: str,
    task_contract: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """Return RuntimeDecision-shaped diagnostic guardrail records.

    These detectors are intentionally passive: they make no policy decisions and
    do not alter runtime behavior. They exist so repeated/no-evidence patterns
    are fixture-testable before any blocking guardrail is enabled.
    """

    tool_rows = _completed_tool_rows(events)
    decisions: list[dict[str, Any]] = []
    decisions.extend(_repeated_identical_args(tool_rows, run_id=run_id))
    decisions.extend(_repeated_failed_calls(tool_rows, run_id=run_id))
    decisions.extend(_idempotent_read_no_progress(tool_rows, run_id=run_id))
    decisions.extend(
        _missing_required_evidence(
            tool_rows,
            run_id=run_id,
            task_contract=task_contract,
        )
    )
    for index, decision in enumerate(decisions, start=1):
        decision.setdefault("decision_id", f"dec_diag_tool_guardrail_{index}")
        decision.setdefault("run_id", run_id)
        decision.setdefault("attempt_id", "trace_summary")
        decision.setdefault("seq", index)
        decision.setdefault("kind", "tool_guardrail")
        decision.setdefault("trigger", "trace_violation")
        decision.setdefault("action", "warn")
        decision.setdefault("status", "failed")
        decision.setdefault("policy_id", "diagnostic_tool_loop_guardrail")
        decision.setdefault("budget", {})
        decision.setdefault("affected_tools", [])
        decision.setdefault("required_evidence", [])
        decision.setdefault("observed_evidence", [])
        decision.setdefault("product_tags", [])
        decision.setdefault("redacted_metadata", {})
    return decisions


def _completed_tool_rows(events: list[dict[str, object]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "tool_call_completed":
            continue
        for tool in event_tools(event_data(event)):
            rows.append(tool)
    return rows


def _repeated_identical_args(
    tool_rows: list[dict[str, Any]], *, run_id: str
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in tool_rows:
        tool_name = _tool_name(row)
        if not tool_name:
            continue
        key = (tool_name, _stable_json(row.get("args") or {}))
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "run_id": run_id,
            "reason": "repeated_identical_tool_args",
            "affected_tools": [tool_name],
            "redacted_metadata": {"repeat_count": count},
        }
        for (tool_name, _args), count in counts.items()
        if count > 1
    ]


def _repeated_failed_calls(
    tool_rows: list[dict[str, Any]], *, run_id: str
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str], int] = {}
    for row in tool_rows:
        tool_name = _tool_name(row)
        if not tool_name or _tool_succeeded(row):
            continue
        error_code = row.get("error_code")
        key = (tool_name, error_code if isinstance(error_code, str) else "failed")
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "run_id": run_id,
            "reason": "repeated_failed_tool_call",
            "affected_tools": [tool_name],
            "redacted_metadata": {"error_code": error_code, "repeat_count": count},
        }
        for (tool_name, error_code), count in counts.items()
        if count > 1
    ]


def _idempotent_read_no_progress(
    tool_rows: list[dict[str, Any]], *, run_id: str
) -> list[dict[str, Any]]:
    counts: dict[tuple[str, str, str], int] = {}
    for row in tool_rows:
        tool_name = _tool_name(row)
        if not _idempotent_read_like(tool_name, row):
            continue
        summary = row.get("result_summary") or row.get("output_preview") or ""
        if not isinstance(summary, str) or not summary.strip():
            continue
        key = (tool_name, _stable_json(row.get("args") or {}), summary.strip()[:240])
        counts[key] = counts.get(key, 0) + 1
    return [
        {
            "run_id": run_id,
            "reason": "idempotent_read_no_progress",
            "affected_tools": [tool_name],
            "redacted_metadata": {"repeat_count": count},
        }
        for (tool_name, _args, _summary), count in counts.items()
        if count > 1
    ]


def _missing_required_evidence(
    tool_rows: list[dict[str, Any]],
    *,
    run_id: str,
    task_contract: dict[str, Any] | None,
) -> list[dict[str, Any]]:
    if not isinstance(task_contract, dict):
        return []
    required_tools = [
        item for item in task_contract.get("required_tools", []) if isinstance(item, str)
    ]
    required_evidence = [
        item
        for item in task_contract.get("required_evidence", [])
        if isinstance(item, str)
    ]
    if not required_tools and not required_evidence:
        return []
    completed_tools = {_tool_name(row) for row in tool_rows if _tool_succeeded(row)}
    missing_tools = [tool for tool in required_tools if tool not in completed_tools]
    observed_evidence = _observed_evidence(tool_rows)
    missing_evidence = [
        item for item in required_evidence if item not in observed_evidence
    ]
    if not missing_tools and not missing_evidence:
        return []
    return [
        {
            "run_id": run_id,
            "reason": "missing_required_tool_evidence",
            "affected_tools": missing_tools,
            "required_evidence": [*missing_tools, *missing_evidence],
            "observed_evidence": sorted(observed_evidence),
            "redacted_metadata": {
                "missing_tools": missing_tools,
                "missing_evidence": missing_evidence,
            },
        }
    ]


def _observed_evidence(tool_rows: list[dict[str, Any]]) -> set[str]:
    observed: set[str] = set()
    for row in tool_rows:
        if not _tool_succeeded(row):
            continue
        tool_name = _tool_name(row)
        if tool_name:
            observed.add(tool_name)
        if row.get("sources"):
            observed.add("source_evidence")
        if row.get("persisted_artifact") or row.get("artifact_refs"):
            observed.add("artifact")
        if row.get("result_preview_paths"):
            observed.add("artifact")
    return observed


def _tool_name(row: dict[str, Any]) -> str:
    value = row.get("tool_name") or row.get("name")
    return value if isinstance(value, str) else ""


def _tool_succeeded(row: dict[str, Any]) -> bool:
    status = row.get("status")
    if status == "completed":
        return row.get("error_code") in {None, ""}
    return False


def _idempotent_read_like(tool_name: str, row: dict[str, Any]) -> bool:
    if row.get("idempotent") is True:
        return True
    side_effect = row.get("side_effect")
    if side_effect == "read_only":
        return True
    return tool_name in _READ_LIKE_TOOLS


def _stable_json(value: object) -> str:
    try:
        return json.dumps(value, sort_keys=True, ensure_ascii=True, default=str)
    except TypeError:
        return repr(value)


__all__ = ["diagnostic_tool_guardrail_decisions"]
