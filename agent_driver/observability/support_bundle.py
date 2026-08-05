"""Redaction-safe support-bundle primitives for runtime/eval workflows."""

from __future__ import annotations

from typing import Any

from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.observability.run_trace.runtime_decisions import (
    runtime_decision_summary,
)
from agent_driver.harness.capability_packs import build_capability_pack_resolution
from agent_driver.observability.provenance import build_provenance_summary
from agent_driver.observability.trace_builder import build_trace_export
from agent_driver.runtime.policy import build_observe_policy_summary
from agent_driver.runtime.supervision import build_run_supervisor_state
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.stream import (
    project_runtime_event_timeline,
    project_runtime_events,
    summarize_run_lifecycle,
    summarize_runtime_session_diagnostics,
)
from agent_driver.contracts.skills_lifecycle import SkillLifecycleCompatibilityReport
from agent_driver.observability.redaction import redact_sensitive_values as _redact_value
from agent_driver.skills.lifecycle import build_skill_support_bundle_projection


def _latest_llm_payload(output: AgentRunOutput, key: str) -> dict[str, Any] | None:
    for event in sorted(output.events, key=lambda item: item.seq, reverse=True):
        if event.type.value != "llm_call_completed":
            continue
        value = event.payload.get(key)
        if isinstance(value, dict):
            return _redact_value(value)
    return None


def build_runtime_support_bundle(output: AgentRunOutput) -> dict[str, Any]:
    """Build support bundle from runtime output with deterministic redaction."""
    trace_export = build_trace_export(output)
    stream_events = project_runtime_events(output.events)
    diagnostics = summarize_runtime_session_diagnostics(
        stream_events,
        durability="runtime_output",
        harness_id=str(output.metadata.get("harness_id"))
        if output.metadata.get("harness_id")
        else None,
        adapter_id=str(output.metadata.get("adapter_id"))
        if output.metadata.get("adapter_id")
        else None,
        session_id=output.thread_id,
    )
    lifecycle = summarize_run_lifecycle(
        stream_events,
        checkpoint_available=output.checkpoint is not None,
        durability="runtime_output",
        harness_id=str(output.metadata.get("harness_id"))
        if output.metadata.get("harness_id")
        else None,
        adapter_id=str(output.metadata.get("adapter_id"))
        if output.metadata.get("adapter_id")
        else None,
        session_id=output.thread_id,
    )
    runtime_decisions = runtime_decision_summary(
        [
            {
                "event": event.type.value,
                "run_id": event.run_id,
                "attempt_id": event.attempt_id,
                "seq": event.seq,
                "data": event.payload,
            }
            for event in output.events
        ],
        run_id=output.run_id,
    )
    provenance = build_provenance_summary(
        events=[
            {
                "event": event.type.value,
                "run_id": event.run_id,
                "attempt_id": event.attempt_id,
                "seq": event.seq,
                "data": event.payload,
            }
            for event in output.events
        ],
        metadata=dict(output.metadata),
        required_evidence=_required_evidence(output.metadata),
    )
    policy_evaluations = build_observe_policy_summary(
        events=[
            {
                "event": event.type.value,
                "run_id": event.run_id,
                "attempt_id": event.attempt_id,
                "seq": event.seq,
                "data": event.payload,
            }
            for event in output.events
        ],
        run_id=output.run_id,
        required_evidence=_required_evidence(output.metadata),
        metadata=dict(output.metadata),
    )
    supervisor_state = build_run_supervisor_state(
        events=[
            {
                "event": event.type.value,
                "run_id": event.run_id,
                "attempt_id": event.attempt_id,
                "seq": event.seq,
                "data": event.payload,
                "created_at": event.created_at,
            }
            for event in output.events
        ],
        run_id=output.run_id,
        policy_summary=policy_evaluations,
        checkpoint_available=output.checkpoint is not None,
        durability="runtime_output",
        session_id=output.thread_id,
        thread_id=output.thread_id,
    )
    capability_pack_resolution = build_capability_pack_resolution(dict(output.metadata))
    skill_lifecycle = _skill_lifecycle_projection(dict(output.metadata))
    return {
        "run": {
            "run_id": output.run_id,
            "attempt_id": output.attempt_id,
            "status": output.status.value,
            "terminal_reason": (
                output.terminal_reason.value if output.terminal_reason else None
            ),
        },
        "trace": trace_export.model_dump(mode="json"),
        "warnings": [item.model_dump(mode="json") for item in output.warnings],
        "tool_trace": [item.model_dump(mode="json") for item in output.tool_trace],
        "subagent_groups": [
            item.model_dump(mode="json") for item in output.subagent_groups
        ],
        "subagent_runs": [
            item.model_dump(mode="json") for item in output.subagent_runs
        ],
        "checkpoint": (
            output.checkpoint.model_dump(mode="json") if output.checkpoint else None
        ),
        "runtime_timeline": {
            "diagnostics": diagnostics.model_dump(mode="json"),
            "rows": [
                row.model_dump(mode="json")
                for row in project_runtime_event_timeline(output.events)
            ],
        },
        "run_lifecycle": lifecycle.model_dump(mode="json"),
        "runtime_decisions": runtime_decisions,
        "policy_evaluations": policy_evaluations,
        "run_supervisor_state": supervisor_state.model_dump(mode="json"),
        "capability_pack_resolution": capability_pack_resolution,
        "skill_lifecycle": skill_lifecycle,
        "validation_gates": build_validation_gate_summary(dict(output.metadata)),
        "goal_state": runtime_decisions["goal_state"],
        **provenance,
        "route_profile": _latest_llm_payload(output, "route_profile"),
        "provider_preflight": _latest_llm_payload(output, "provider_preflight"),
        "metadata": _redact_value(output.metadata),
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }


def build_persisted_support_bundle(persisted_replay: dict[str, Any]) -> dict[str, Any]:
    """Build support bundle from replay payload loaded from persistent stores."""
    events = persisted_replay.get("events")
    redacted_events = _redact_value(events if isinstance(events, list) else [])
    runtime_decisions = runtime_decision_summary(
        _persisted_events_for_summary(
            redacted_events if isinstance(redacted_events, list) else []
        ),
        run_id=str(persisted_replay.get("run_id") or "persisted_replay"),
    )
    metadata = _redact_value(persisted_replay.get("metadata", {}))
    provenance = build_provenance_summary(
        events=_persisted_events_for_summary(
            redacted_events if isinstance(redacted_events, list) else []
        ),
        metadata=metadata if isinstance(metadata, dict) else {},
        required_evidence=_required_evidence(
            metadata if isinstance(metadata, dict) else {}
        ),
    )
    policy_evaluations = build_observe_policy_summary(
        events=_persisted_events_for_summary(
            redacted_events if isinstance(redacted_events, list) else []
        ),
        run_id=str(persisted_replay.get("run_id") or "persisted_replay"),
        required_evidence=_required_evidence(
            metadata if isinstance(metadata, dict) else {}
        ),
        metadata=metadata if isinstance(metadata, dict) else {},
    )
    supervisor_state = build_run_supervisor_state(
        events=_persisted_events_for_summary(
            redacted_events if isinstance(redacted_events, list) else []
        ),
        run_id=str(persisted_replay.get("run_id") or "persisted_replay"),
        policy_summary=policy_evaluations,
        checkpoint_available=bool(persisted_replay.get("latest_checkpoint")),
        durability="persisted_replay",
    )
    capability_pack_resolution = build_capability_pack_resolution(
        metadata if isinstance(metadata, dict) else {}
    )
    skill_lifecycle = _skill_lifecycle_projection(
        metadata if isinstance(metadata, dict) else {}
    )
    return {
        "run": {
            "run_id": persisted_replay.get("run_id"),
            "event_count": int(persisted_replay.get("event_count", 0)),
            "trajectory": persisted_replay.get("trajectory", []),
        },
        "latest_checkpoint": persisted_replay.get("latest_checkpoint"),
        "checkpoints": persisted_replay.get("checkpoints", []),
        "events": redacted_events,
        "metadata": metadata,
        "runtime_timeline": {
            "diagnostics": {
                "run_id": persisted_replay.get("run_id"),
                "durability": "persisted_replay",
                "timeline_row_count": 0,
                "last_seq": _last_persisted_seq(
                    events if isinstance(events, list) else []
                ),
                "reconnect_cursor": _persisted_reconnect_cursor(
                    persisted_replay.get("run_id"),
                    events if isinstance(events, list) else [],
                ),
                "redaction": {"safe_by_default": True},
            },
        },
        "run_lifecycle": {
            "run_id": persisted_replay.get("run_id"),
            "state": "unknown",
            "last_seq": _last_persisted_seq(events if isinstance(events, list) else []),
            "reconnect_cursor": _persisted_reconnect_cursor(
                persisted_replay.get("run_id"),
                events if isinstance(events, list) else [],
            ),
            "support_bundle_available": True,
        },
        "runtime_decisions": runtime_decisions,
        "policy_evaluations": policy_evaluations,
        "run_supervisor_state": supervisor_state.model_dump(mode="json"),
        "capability_pack_resolution": capability_pack_resolution,
        "skill_lifecycle": skill_lifecycle,
        "validation_gates": build_validation_gate_summary(
            metadata if isinstance(metadata, dict) else {}
        ),
        "goal_state": runtime_decisions["goal_state"],
        **provenance,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_prompt": False,
            "contains_raw_tool_outputs": False,
        },
    }


def _persisted_events_for_summary(events: list[Any]) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for event in events:
        if not isinstance(event, dict):
            continue
        event_name = event.get("event") or event.get("type")
        data = event.get("data") or event.get("payload")
        rows.append(
            {
                "event": event_name,
                "run_id": event.get("run_id"),
                "attempt_id": event.get("attempt_id"),
                "seq": event.get("seq"),
                "data": data if isinstance(data, dict) else {},
            }
        )
    return rows


def _last_persisted_seq(events: list[Any]) -> int | None:
    seqs = [event.get("seq") for event in events if isinstance(event, dict)]
    ints = [seq for seq in seqs if isinstance(seq, int)]
    return max(ints) if ints else None


def _persisted_reconnect_cursor(run_id: object, events: list[Any]) -> str | None:
    last_seq = _last_persisted_seq(events)
    if not isinstance(run_id, str) or last_seq is None:
        return None
    return f"{run_id}:{last_seq}"


def _required_evidence(metadata: dict[str, Any]) -> list[str]:
    value = metadata.get("required_evidence")
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if str(item).strip()]


def _skill_lifecycle_projection(metadata: dict[str, Any]) -> dict[str, Any] | None:
    raw = metadata.get("skill_lifecycle_report") or metadata.get(
        "skill_lifecycle_compatibility_report"
    )
    if not isinstance(raw, dict):
        return None
    report = SkillLifecycleCompatibilityReport.model_validate(raw)
    return build_skill_support_bundle_projection(report)


__all__ = ["build_persisted_support_bundle", "build_runtime_support_bundle"]
