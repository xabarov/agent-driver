"""Tool-stage runtime-event emitters (extracted from tool_stage/__init__.py).

Leaf module: tool started/progress/completed events, tool-policy RuntimeDecisions,
and artifact events. A closed cluster that only calls emit_step_event/host._emit
and external persist/research helpers (host typed ``Any`` so it never imports the
ToolStageHost protocol) — the import stays one-way (tool_stage -> events).
"""

from __future__ import annotations
from typing import Any
from agent_driver.contracts.enums import (
    GuardrailDecision,
    RuntimeEventType,
    ToolPolicyDecision,
)
from agent_driver.observability.source_evidence import source_evidence_from_tool_result
from agent_driver.runtime.artifact_events import artifact_event_from_tool_result
from agent_driver.runtime.tool_gate import (
    RESERVED_GATE_DECISION_KEY,
    extract_reserved_metadata,
)
from agent_driver.runtime.metadata_state import (
    get_tool_loop_state,
)
from agent_driver.runtime.research_artifacts import (
    persist_deep_research_claims_matrix,
    persist_deep_research_source_ledger,
)
from agent_driver.runtime.research_evidence import (
    research_evidence_from_tool_results,
    research_source_ledger_from_tool_results,
    rollup_child_source_ledgers,
)
from agent_driver.runtime.research_session_contract import (
    child_source_ledgers_from_context,
)
from agent_driver.runtime.single_agent.lifecycle.events import emit_step_event
from agent_driver.runtime.single_agent.planning.state import (
    build_planning_snapshot,
)
from agent_driver.runtime.single_agent.tool_stage.protocol_messages import (
    _compact_generic_tool_payload_for_protocol,  # noqa: F401 - compatibility re-export
    _is_drop_candidate_assistant_message,  # noqa: F401 - compatibility re-export
)
from agent_driver.runtime.single_agent.types import (
    RunContext,
)
from agent_driver.runtime.tools import ToolExecutionResult
from agent_driver.tools.executor.planned import extract_planned_tool_calls
from agent_driver.runtime.single_agent.tool_stage.guards import (  # noqa: F401 - re-export for compat
    _POLICY_READ_LIKE_TOOLS,
    _PROGRESS_ONLY_TOOL_NAMES,
    _TOOL_FAILURE_GUARD_THRESHOLD,
    _current_no_progress_repeat,
    _deliverable_request_should_force_final,
    _force_final_reason,
    _has_repeated_recent_tool_call,
    _has_successful_python_result,
    _maybe_enforce_tool_loop_policy,
    _maybe_force_final_answer,
    _python_reliability_request_active,
    _python_reliability_request_pending,
    _python_request_should_force_final,
    _refresh_force_final_controls,
    _resolved_budget,
    _should_force_final_answer,
    _tool_loop_policy_threshold,
    _tool_no_progress_key,
    _update_tool_failure_guard,
)


def _emit_tool_progress_events(
    host: Any, context: RunContext, result: ToolExecutionResult
) -> None:
    """EPIC-04 WP-B: project each captured tool-progress entry into a
    ``RuntimeEventType.TOOL_PROGRESS`` runtime event, correlated by tool_call_id.

    This is what makes a handler's ``report_tool_progress`` (and a backend job's
    bounded observed events, surfaced through it) reach the runtime event log /
    stream projection. No progress entries ⇒ no events (zero overhead)."""
    for entry in getattr(result, "progress_events", None) or []:
        progress = getattr(entry, "progress", None)
        if progress is None:
            continue
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.TOOL_PROGRESS,
            payload={
                "tool_call_id": getattr(entry, "tool_call_id", None),
                "tool_name": getattr(entry, "tool_name", None),
                "call_index": getattr(entry, "call_index", None),
                "kind": getattr(progress, "kind", None),
                "message": getattr(progress, "message", None),
                "completion_ratio": getattr(progress, "completion_ratio", None),
            },
        )


def _emit_tool_completed_if_needed(
    host: Any, context: RunContext, result: ToolExecutionResult
) -> None:
    if not result.traces:
        return
    planned_calls = (
        extract_planned_tool_calls(context.llm_response) if context.llm_response else []
    )
    args_by_call_id = {
        call.tool_call_id: call.args
        for call in planned_calls
        if isinstance(call.tool_call_id, str) and call.tool_call_id
    }
    fallback_args = [call.args for call in planned_calls]
    preview_paths_by_call_id: dict[str, list[str]] = {}
    fallback_preview_paths: list[list[str]] = []
    for envelope in result.envelopes:
        preview_paths: list[str] = []
        structured = envelope.structured_output
        if isinstance(structured, dict):
            if envelope.call.tool_name == "glob_search" and isinstance(
                structured.get("results"), list
            ):
                preview_paths = [
                    str(item) for item in structured["results"] if isinstance(item, str)
                ][:5]
            elif envelope.call.tool_name == "web_search" and isinstance(
                structured.get("result_preview_urls"), list
            ):
                preview_paths = [
                    str(item)
                    for item in structured["result_preview_urls"]
                    if isinstance(item, str)
                ][:5]
        fallback_preview_paths.append(preview_paths)
        if (
            isinstance(envelope.call.tool_call_id, str)
            and envelope.call.tool_call_id
            and preview_paths
        ):
            preview_paths_by_call_id[envelope.call.tool_call_id] = preview_paths
    tools = []
    for index, trace in enumerate(result.traces):
        row: dict[str, object] = {
            "tool_name": trace.tool_name,
            "tool_call_id": trace.tool_call_id,
            "args": (
                args_by_call_id.get(trace.tool_call_id)
                if isinstance(trace.tool_call_id, str) and trace.tool_call_id
                else (fallback_args[index] if index < len(fallback_args) else {})
            ),
            "status": trace.status.value,
            "result_summary": trace.result_summary,
            "output_preview": (
                trace.result_summary[:240]
                if isinstance(trace.result_summary, str)
                else None
            ),
            "structured_output": None,
            "error_code": trace.error_code,
            "truncated": trace.truncated,
            "result_preview_paths": (
                preview_paths_by_call_id.get(trace.tool_call_id, [])
                if isinstance(trace.tool_call_id, str) and trace.tool_call_id
                else (
                    fallback_preview_paths[index]
                    if index < len(fallback_preview_paths)
                    else []
                )
            ),
            "risk": trace.risk.value,
            "side_effect": trace.side_effect.value,
            "approval_mode": trace.approval_mode.value,
        }
        if index < len(result.envelopes):
            envelope = result.envelopes[index]
            structured = envelope.structured_output
            if isinstance(structured, dict):
                row["structured_output"] = structured
                remediation = structured.get("remediation")
                if isinstance(remediation, str) and remediation.strip():
                    row["remediation"] = remediation.strip()
                persisted_artifact = structured.get("persisted_artifact")
                if isinstance(persisted_artifact, dict):
                    row["persisted_artifact"] = dict(persisted_artifact)
                    workspace_path = persisted_artifact.get("workspace_path")
                    if isinstance(workspace_path, str) and workspace_path:
                        row["result_preview_paths"] = [workspace_path]
                if trace.status.value == "completed":
                    sources = source_evidence_from_tool_result(
                        tool_name=envelope.call.tool_name,
                        structured_output=structured,
                        tool_call_id=envelope.call.tool_call_id,
                    )
                    if sources:
                        row["sources"] = sources
        tools.append(row)
    payload: dict[str, object] = {
        "tool_calls": len(result.traces),
        "statuses": [trace.status.value for trace in result.traces],
        "tools": tools,
    }
    snapshot = build_planning_snapshot(context)
    if snapshot is not None:
        payload["planning_snapshot"] = snapshot
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_COMPLETED,
        payload=payload,
    )
    _emit_tool_policy_runtime_decisions(host, context, result)
    _emit_artifact_events_from_tool_result(host, context, result)
    tool_results_rows = get_tool_loop_state(context).tool_results()
    child_ledgers = child_source_ledgers_from_context(context)
    merged_ledger, _ = rollup_child_source_ledgers(
        research_source_ledger_from_tool_results(tool_results_rows),
        research_evidence_from_tool_results(tool_results_rows),
        child_ledgers,
    )
    source_ledger = merged_ledger.model_dump()
    parent_used_source_tool = any(
        row["tool_name"]
        in {"web_search", "web_fetch", "source_read", "pdf_read", "browser_read"}
        for row in tools
        if isinstance(row.get("tool_name"), str)
    )
    # Persist when the parent itself read sources this turn OR when joined
    # children contributed verified reads — otherwise a delegating parent that
    # never fetches would never emit sources.jsonl/claims.jsonl despite real
    # child evidence.
    child_contributed_reads = bool(child_ledgers) and bool(
        merged_ledger.verified_reads
        or merged_ledger.blocked_reads
        or merged_ledger.failed_reads
    )
    if parent_used_source_tool or child_contributed_reads:
        source_artifact = persist_deep_research_source_ledger(context, source_ledger)
        if source_artifact is not None:
            source_ledger["artifact"] = {
                "path": source_artifact["path"],
                "record_count": source_artifact["record_count"],
                "size_bytes": source_artifact["size_bytes"],
            }
            emit_step_event(
                host,
                context,
                event_type=(
                    RuntimeEventType.ARTIFACT_CREATED
                    if source_artifact.get("created") is True
                    else RuntimeEventType.ARTIFACT_UPDATED
                ),
                payload={
                    "path": source_artifact["path"],
                    "kind": source_artifact["kind"],
                    "operation": source_artifact["operation"],
                    "tool_name": "source_ledger",
                    "size_bytes": source_artifact["size_bytes"],
                    "bytes": source_artifact["bytes"],
                    "record_count": source_artifact["record_count"],
                },
            )
        claims_artifact = persist_deep_research_claims_matrix(context, source_ledger)
        if claims_artifact is not None:
            emit_step_event(
                host,
                context,
                event_type=(
                    RuntimeEventType.ARTIFACT_CREATED
                    if claims_artifact.get("created") is True
                    else RuntimeEventType.ARTIFACT_UPDATED
                ),
                payload={
                    "path": claims_artifact["path"],
                    "kind": claims_artifact["kind"],
                    "operation": claims_artifact["operation"],
                    "tool_name": "claims_matrix",
                    "size_bytes": claims_artifact["size_bytes"],
                    "bytes": claims_artifact["bytes"],
                    "record_count": claims_artifact["record_count"],
                    "verified_count": claims_artifact["verified_count"],
                    "unsupported_count": claims_artifact["unsupported_count"],
                    "inaccessible_count": claims_artifact.get("inaccessible_count", 0),
                },
            )
        emit_step_event(
            host,
            context,
            event_type=RuntimeEventType.SOURCE_LEDGER_UPDATED,
            payload=source_ledger,
        )


def _emit_tool_policy_runtime_decisions(
    host: Any, context: RunContext, result: ToolExecutionResult
) -> None:
    for envelope in result.envelopes:
        tool_name = envelope.call.tool_name
        metadata: dict[str, object] = {
            "tool_call_id": envelope.call.tool_call_id,
            "policy_decision": envelope.decision.value,
            "guardrail_decision": envelope.guardrail_decision.value,
        }
        if envelope.error is not None:
            metadata["error_code"] = envelope.error.code
        # R1 — fold reserved (``_ad_``) gate provenance/decision carried on the
        # envelope into the trace-safe RuntimeDecision projection, and let a gate
        # decision be told apart from a static policy decision. Reserved keys are
        # host-authored + bounded; model/tool metadata cannot forge them.
        reserved = extract_reserved_metadata(envelope.metadata)
        if reserved:
            metadata = {**metadata, **reserved}
        gate_decision = reserved.get(RESERVED_GATE_DECISION_KEY)
        if envelope.decision == ToolPolicyDecision.DENY:
            gated = gate_decision == "deny"
            # ``kind``/``action`` are taxonomy-validated; the gate-vs-static
            # distinction rides on the free-form ``policy_id``/``trigger``/
            # ``reason`` so a host can attribute the denial without inspecting
            # reason strings.
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_gate_denied" if gated else "tool_denied",
                action="block",
                reason="tool_gate_denied" if gated else "tool_policy_denied",
                policy_id="tool_gate" if gated else "tool_policy",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        elif envelope.decision == ToolPolicyDecision.INTERRUPT:
            gated = gate_decision == "ask"
            host._emit_runtime_decision(
                context,
                kind="approval",
                trigger="tool_gate_ask" if gated else "tool_denied",
                action="interrupt",
                reason="tool_gate_ask" if gated else "tool_policy_interrupt",
                policy_id="tool_gate" if gated else "tool_policy",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        if envelope.guardrail_decision == GuardrailDecision.BLOCK:
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_denied",
                action="block",
                reason="tool_guardrail_blocked",
                policy_id="tool_guardrail",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )
        elif envelope.guardrail_decision == GuardrailDecision.SANITIZE:
            host._emit_runtime_decision(
                context,
                kind="tool_guardrail",
                trigger="tool_completed",
                action="warn",
                reason="tool_guardrail_sanitized",
                policy_id="tool_guardrail",
                affected_tools=[tool_name],
                redacted_metadata=metadata,
            )


def _emit_artifact_events_from_tool_result(
    host: Any, context: RunContext, result: ToolExecutionResult
) -> None:
    for envelope in result.envelopes:
        event = artifact_event_from_tool_result(context, envelope)
        if event is None:
            continue
        event_type, payload = event
        emit_step_event(host, context, event_type=event_type, payload=payload)


def _emit_tool_started_if_needed(host: Any, context: RunContext) -> None:
    response = context.llm_response
    if response is None:
        return
    calls = extract_planned_tool_calls(response)
    if not calls:
        return
    emit_step_event(
        host,
        context,
        event_type=RuntimeEventType.TOOL_CALL_STARTED,
        payload={
            "tool_calls": len(calls),
            "tools": [
                {
                    "tool_name": call.tool_name,
                    "tool_call_id": call.tool_call_id,
                    "args": call.args,
                }
                for call in calls
            ],
        },
    )
