"""Loop-guard plane for the tool stage (epic 023 refactor batch A1).

Extracted verbatim from ``tool_stage/__init__.py``: the independent loop
guards — tool-failure streak guard (epic 019 B), tool no-progress loop
policy, and the force-final controls/budget resolution family. Behavior is
byte-for-byte identical; ``tool_stage/__init__`` re-exports these names so all
existing callers and tests keep working.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import json

from agent_driver.contracts.enums import (
    RuntimeEventType,
)
from agent_driver.runtime.metadata_state import (
    get_research_runtime_state,
    get_tool_loop_state,
)
from agent_driver.runtime.policy import policy_profile_from_metadata
from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    build_research_session_contract_from_context,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.tool_stage.research import (
    research_request_should_force_final,
    source_verified_research_pending,
)
from agent_driver.runtime.single_agent.types import (
    DEFAULT_MAX_STEPS_BACKSTOP,
    DEFAULT_MAX_TOOL_CALLS_BACKSTOP,
    RunContext,
)
from agent_driver.runtime.tools import ToolExecutionResult


# Epic 015: bound the empty-answer re-prompt so a provider that keeps returning empty can't spin.


if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.tool_stage import ToolStageHost

# Consecutive same-signature tool FAILURES before the loop is forced to finalize
# (epic 019 phase B; reference: openclaude toolFailureLoopGuard, threshold 3 with a
# warn-before-stop). Distinct from repeated-identical-CALL detection: here the calls
# may vary, but the same tool keeps failing with the same error code.
_TOOL_FAILURE_GUARD_THRESHOLD = 3


def _update_tool_failure_guard(
    host: ToolStageHost, context: RunContext, result: ToolExecutionResult
) -> None:
    """Track consecutive same-signature tool failures; warn, then force final."""
    if not result.envelopes:
        return
    state = context.metadata.get("tool_failure_guard")
    state = dict(state) if isinstance(state, dict) else {}
    signature = state.get("signature")
    count = int(state.get("count", 0) or 0)
    # Epic 019 phase B fix (reference: openclaude 9d5b77d): count once per TURN, not
    # per failure event. A parallel fan-out of N calls failing with the same signature
    # in one turn must advance the streak by 1 — the threshold measures "turns that
    # failed to adapt", and the model hasn't seen any of this turn's results yet.
    turn_signatures: list[str] = []
    for envelope in result.envelopes:
        if envelope.error is None:
            continue
        next_signature = f"{envelope.call.tool_name}:{envelope.error.code}"
        if next_signature not in turn_signatures:
            turn_signatures.append(next_signature)
    if not turn_signatures:
        # A fully successful round breaks the streak.
        context.metadata["tool_failure_guard"] = {"signature": None, "count": 0}
        return
    if signature in turn_signatures:
        count += 1  # the prior failing signature persisted this turn — one increment
    else:
        signature, count = turn_signatures[-1], 1
    context.metadata["tool_failure_guard"] = {"signature": signature, "count": count}
    if count == _TOOL_FAILURE_GUARD_THRESHOLD - 1:
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.WARNING,
            payload={
                "warning": (
                    f"Tool '{signature}' failed {count} times in a row; one more "
                    "identical failure forces the final answer."
                ),
                "signal_id": "tool_failure_streak_warning",
                "severity": "warning",
                "signature": signature,
                "count": count,
                "threshold": _TOOL_FAILURE_GUARD_THRESHOLD,
            },
        )
    elif count >= _TOOL_FAILURE_GUARD_THRESHOLD:
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.WARNING,
            payload={
                "warning": (
                    f"Tool '{signature}' failed {count} times in a row; forcing the "
                    "final answer from the evidence gathered so far."
                ),
                "signal_id": "tool_failure_streak_force_final",
                "severity": "warning",
                "signature": signature,
                "count": count,
            },
        )


def _maybe_enforce_tool_loop_policy(
    host: ToolStageHost,
    context: RunContext,
    result: ToolExecutionResult,
) -> None:
    profile = policy_profile_from_metadata(context.run_input.app_metadata)
    if profile is None or profile.mode not in {"enforce", "fail_closed"}:
        return
    enabled = set(profile.enabled_policy_ids)
    if enabled and "tool_loop_no_progress" not in enabled:
        return
    repeat = _current_no_progress_repeat(context, result)
    if repeat is None:
        return
    threshold = _tool_loop_policy_threshold(
        profile.budgets.get("tool_loop_no_progress")
    )
    if repeat["repeat_count"] < threshold:
        return
    reason = "policy_tool_loop_no_progress_force_final"
    get_tool_loop_state(context).force_final_answer(reason=reason)
    host._emit_runtime_decision(
        context,
        kind="tool_guardrail",
        trigger="tool_completed",
        action="force_final",
        reason=reason,
        status="applied",
        policy_id="tool_loop_no_progress",
        budget={"repeat_threshold": threshold},
        affected_tools=[str(repeat["tool_name"])],
        redacted_metadata={
            "policy_profile_id": profile.profile_id,
            "policy_mode": profile.mode,
            "selected_policy_action": "force_final",
            "repeat_count": int(repeat["repeat_count"]),
            "args_key": repeat["args_key"],
        },
    )


def _tool_loop_policy_threshold(raw: object) -> int:
    if not isinstance(raw, dict):
        return 2
    value = raw.get("repeat_threshold", raw.get("max_repeats"))
    if isinstance(value, int) and value > 1:
        return value
    return 2


def _current_no_progress_repeat(
    context: RunContext,
    result: ToolExecutionResult,
) -> dict[str, object] | None:
    current_keys = {
        key
        for envelope in result.envelopes
        if (
            key := _tool_no_progress_key(
                envelope.call.tool_name, envelope.call.args, envelope.summary
            )
        )
        is not None
    }
    if not current_keys:
        return None
    counts: dict[tuple[str, str, str], int] = {}
    for item in get_tool_loop_state(context).tool_results():
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "")
        args = call.get("args")
        summary = item.get("summary")
        key = _tool_no_progress_key(tool_name, args, summary)
        if key is None:
            continue
        counts[key] = counts.get(key, 0) + 1
    matches = [
        (key, count)
        for key, count in counts.items()
        if key in current_keys and count > 1
    ]
    if not matches:
        return None
    (tool_name, args_key, summary_key), repeat_count = max(
        matches,
        key=lambda item: item[1],
    )
    return {
        "tool_name": tool_name,
        "args_key": args_key,
        "summary_key": summary_key,
        "repeat_count": repeat_count,
    }


_POLICY_READ_LIKE_TOOLS = {
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


def _tool_no_progress_key(
    tool_name: str,
    args: object,
    summary: object,
) -> tuple[str, str, str] | None:
    if tool_name not in _POLICY_READ_LIKE_TOOLS:
        return None
    if not isinstance(summary, str) or not summary.strip():
        return None
    try:
        args_key = json.dumps(
            args or {}, sort_keys=True, ensure_ascii=True, default=str
        )
    except TypeError:
        args_key = repr(args)
    return (tool_name, args_key, summary.strip()[:240])


def _refresh_force_final_controls(context: RunContext) -> None:
    """Clear forced-final flags unless current run state still requires them."""
    reason = _force_final_reason(context)
    tool_state = get_tool_loop_state(context)
    if reason is not None:
        tool_state.ensure_force_final_answer(reason=reason)
        return
    tool_state.clear_force_final_answer()


def _maybe_force_final_answer(context: RunContext) -> None:
    """Enable forced final-answer mode only when guard heuristics trigger."""
    reason = _force_final_reason(context)
    tool_state = get_tool_loop_state(context)
    if reason is not None:
        tool_state.ensure_force_final_answer(reason=reason)
        return
    tool_state.clear_force_final_answer()


def _should_force_final_answer(context: RunContext) -> bool:
    """Return whether loop should force final answer on next LLM request."""
    return _force_final_reason(context) is not None


def _resolved_budget(raw: object, meta_value: object, backstop: int) -> int:
    """Per-run budget wins; else the runner default stamped into metadata; else backstop.

    The previous fallback was a hardcoded 1: hosts that omitted per-run budgets got an
    agent forced to finalize after its FIRST tool call, while the documented
    RunnerConfig backstops (default_max_steps=80) never reached this check
    (observations.md 2026-07-18). ``None`` in metadata (unbounded opt-in) still uses
    the backstop HERE — force-final is about eventually producing an answer; the loop
    terminal honors unbounded separately in the journal.
    """
    if isinstance(raw, int):
        return max(1, raw)
    if isinstance(meta_value, int):
        return max(1, meta_value)
    return max(1, backstop)


def _force_final_reason(context: RunContext) -> str | None:
    """Return why the next LLM call should produce a final answer, if needed."""
    max_tool_calls = _resolved_budget(
        context.run_input.max_tool_calls,
        context.metadata.get("max_tool_calls"),
        DEFAULT_MAX_TOOL_CALLS_BACKSTOP,
    )
    max_steps = _resolved_budget(
        context.run_input.max_steps,
        context.metadata.get("max_steps"),
        DEFAULT_MAX_STEPS_BACKSTOP,
    )
    failure_guard = context.metadata.get("tool_failure_guard")
    if (
        isinstance(failure_guard, dict)
        and int(failure_guard.get("count", 0) or 0) >= _TOOL_FAILURE_GUARD_THRESHOLD
    ):
        return "tool_failure_streak"
    effective_tool_calls = max(
        0,
        context.tool_calls - int(context.metadata.get("refunded_tool_calls", 0) or 0),
    )
    near_tool_budget = effective_tool_calls >= max(1, max_tool_calls - 1)
    near_step_budget = context.llm_step_count >= max(1, max_steps - 1)
    loop_detected = _has_repeated_recent_tool_call(context)
    zero_streak = int(context.metadata.get("web_search_zero_streak", 0))
    zero_results_triggered = zero_streak >= 1
    if near_tool_budget:
        return "near_tool_budget"
    if near_step_budget:
        return "near_step_budget"
    research_satisfied = research_request_should_force_final(
        context
    ) and not _python_reliability_request_pending(context)
    if research_satisfied:
        contract = build_research_session_contract_from_context(
            context,
            enforce_final_source_links=False,
            enforce_todos=False,
            allow_final_deliverable_todos=True,
        )
        get_research_runtime_state(context).set_contract(
            payload=contract.model_dump(),
            status=FINAL_READINESS_ALLOWED,
            reasons=[],
        )
        return "research_request_satisfied"
    contract = build_research_session_contract_from_context(
        context,
        enforce_final_source_links=False,
        allow_final_deliverable_todos=True,
    )
    research_state = get_research_runtime_state(context)
    research_state.set_contract_payload(contract.model_dump())
    if contract.final_readiness.status != FINAL_READINESS_ALLOWED:
        research_state.set_contract(
            payload=contract.model_dump(),
            status=contract.final_readiness.status,
            reasons=list(contract.final_readiness.reasons),
        )
        return None
    research_state.set_contract(
        payload=contract.model_dump(),
        status=contract.final_readiness.status,
        reasons=[],
    )
    if loop_detected:
        return "repeated_tool_call"
    if zero_results_triggered:
        return "web_search_zero_results"
    deliverable_requested = _deliverable_request_should_force_final(context)
    python_result_ready = _python_request_should_force_final(context)
    if deliverable_requested:
        return "deliverable_request_satisfied"
    if python_result_ready:
        return "python_result_ready"
    return None


_PROGRESS_ONLY_TOOL_NAMES = {
    "planning_state_update",
    "todo_write",
    "ask_user_question",
    "enter_plan_mode",
    "exit_plan_mode_v2",
}


def _deliverable_request_should_force_final(context: RunContext) -> bool:
    deliverable = context.run_input.tool_policy.metadata.get("deliverable_request")
    if not isinstance(deliverable, dict) or deliverable.get("enabled") is not True:
        return False
    if source_verified_research_pending(context):
        return False
    tool_results = get_tool_loop_state(context).tool_results()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if not tool_name or tool_name == "ask_user_question":
            continue
        if tool_name not in _PROGRESS_ONLY_TOOL_NAMES:
            return True
        if tool_name in {"todo_write", "planning_state_update"}:
            return True
    return False


def _python_request_should_force_final(context: RunContext) -> bool:
    if not _python_reliability_request_active(context):
        return False
    return _has_successful_python_result(context)


def _python_reliability_request_pending(context: RunContext) -> bool:
    return _python_reliability_request_active(
        context
    ) and not _has_successful_python_result(context)


def _python_reliability_request_active(context: RunContext) -> bool:
    python_policy = context.run_input.tool_policy.metadata.get(
        "python_reliability_request"
    )
    return isinstance(python_policy, dict) and python_policy.get("enabled") is True


def _has_successful_python_result(context: RunContext) -> bool:
    tool_results = get_tool_loop_state(context).tool_results()
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        if call.get("tool_name") != "python":
            continue
        if item.get("error"):
            continue
        summary = str(item.get("summary") or item.get("result_summary") or "").lower()
        if (
            "python policy" in summary
            or "imports blocked by sandbox" in summary
            or "unauthorized import" in summary
        ):
            continue
        return True
    return False


def _has_repeated_recent_tool_call(context: RunContext) -> bool:
    """Detect two latest tool calls with identical tool name and args."""
    tool_results = get_tool_loop_state(context).tool_results()
    recent: list[tuple[str, str]] = []
    for item in tool_results:
        if not isinstance(item, dict):
            continue
        call = item.get("call")
        if not isinstance(call, dict):
            continue
        tool_name = str(call.get("tool_name") or "").strip()
        if not tool_name:
            continue
        args = call.get("args")
        args_key = json.dumps(args, ensure_ascii=True, sort_keys=True)
        recent.append((tool_name, args_key))
    if len(recent) < 2:
        return False
    return recent[-1] == recent[-2]
