"""Runtime decision projection for run trace summaries."""

from __future__ import annotations

from typing import Any

from agent_driver.observability.run_trace.tools import dedupe_preserve_order, event_data
from agent_driver.observability.run_trace.tool_guardrails import (
    diagnostic_tool_guardrail_decisions,
)


_SAFE_KEYS = (
    "decision_id",
    "run_id",
    "attempt_id",
    "seq",
    "kind",
    "trigger",
    "action",
    "reason",
    "status",
    "goal_id",
    "policy_id",
    "budget",
    "affected_tools",
    "required_evidence",
    "observed_evidence",
    "product_tags",
    "redacted_metadata",
)


def runtime_decision_summary(
    events: list[dict[str, object]],
    *,
    run_id: str = "trace_summary",
    task_contract: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Summarize trace-safe runtime decisions from persisted events."""

    decisions: list[dict[str, Any]] = []
    for event in events:
        if event.get("event") != "runtime_decision":
            continue
        data = event_data(event)
        decision = {key: data[key] for key in _SAFE_KEYS if key in data}
        if not decision:
            continue
        decisions.append(decision)
    max_seq = max(
        [int(item.get("seq") or 0) for item in decisions if isinstance(item, dict)]
        or [0]
    )
    for index, decision in enumerate(
        diagnostic_tool_guardrail_decisions(
            events,
            run_id=run_id,
            task_contract=task_contract,
        ),
        start=1,
    ):
        decision["seq"] = max_seq + index
        decisions.append({key: decision[key] for key in _SAFE_KEYS if key in decision})
    decisions.sort(key=lambda item: int(item.get("seq") or 0))

    counts_by_kind: dict[str, int] = {}
    counts_by_action: dict[str, int] = {}
    counts_by_status: dict[str, int] = {}
    retry_counts: dict[str, int] = {}
    unsatisfied: list[str] = []
    goal_state = {
        "status": "inactive",
        "goal_id": None,
        "last_decision_id": None,
        "unsatisfied_requirements": [],
    }

    for decision in decisions:
        kind = _as_str(decision.get("kind"))
        action = _as_str(decision.get("action"))
        status = _as_str(decision.get("status"))
        reason = _as_str(decision.get("reason"))
        goal_id = _as_str(decision.get("goal_id"))
        if kind:
            counts_by_kind[kind] = counts_by_kind.get(kind, 0) + 1
        if action:
            counts_by_action[action] = counts_by_action.get(action, 0) + 1
        if status:
            counts_by_status[status] = counts_by_status.get(status, 0) + 1
        if action == "retry" and reason:
            retry_counts[reason] = retry_counts.get(reason, 0) + 1
        if status == "failed":
            unsatisfied.append(reason or kind or "runtime_decision_failed")
        if goal_id or kind == "goal":
            goal_state["status"] = _goal_status_from_decision(decision)
            goal_state["goal_id"] = goal_id
            goal_state["last_decision_id"] = _as_str(decision.get("decision_id"))

    unsatisfied = dedupe_preserve_order([item for item in unsatisfied if item])
    goal_state["unsatisfied_requirements"] = unsatisfied
    return {
        "count": len(decisions),
        "counts_by_kind": counts_by_kind,
        "counts_by_action": counts_by_action,
        "counts_by_status": counts_by_status,
        "retry_counts": retry_counts,
        "last_decision": decisions[-1] if decisions else None,
        "unsatisfied_requirements": unsatisfied,
        "goal_state": goal_state,
        "decisions": decisions[-20:],
    }


def _goal_status_from_decision(decision: dict[str, Any]) -> str:
    action = _as_str(decision.get("action"))
    status = _as_str(decision.get("status"))
    if action == "mark_achieved" or status == "satisfied":
        return "achieved"
    if action == "mark_blocked":
        return "blocked"
    if action == "interrupt":
        return "paused"
    if action in {"block", "ask_user"}:
        return "blocked"
    return "active"


def _as_str(value: object) -> str:
    return value if isinstance(value, str) else ""


__all__ = ["runtime_decision_summary"]
