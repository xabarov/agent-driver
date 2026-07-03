"""Tests for observability support-bundle primitives."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.observability import (
    build_persisted_support_bundle,
    build_runtime_support_bundle,
)
from agent_driver.skills.lifecycle import seed_chat_demo_skill_lifecycle_report


def test_runtime_support_bundle_redacts_sensitive_metadata_keys() -> None:
    """Runtime support bundle should redact secret-bearing metadata fields."""
    output = AgentRunOutput(
        run_id="run_obs_bundle_1",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_1",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
            )
        ],
        metadata={
            "api_key": "abc",
            "nested": {"token": "secret-token", "safe": "ok"},
        },
    )
    bundle = build_runtime_support_bundle(output)
    assert bundle["metadata"]["api_key"] == "<redacted>"
    assert bundle["metadata"]["nested"]["token"] == "<redacted>"
    assert bundle["metadata"]["nested"]["safe"] == "ok"
    assert bundle["runtime_timeline"]["diagnostics"]["run_id"] == "run_obs_bundle_1"
    assert (
        bundle["runtime_timeline"]["diagnostics"]["terminal_event"] == "run_completed"
    )
    assert bundle["runtime_timeline"]["rows"][0]["category"] == "lifecycle"
    assert bundle["run_lifecycle"]["state"] == "completed"
    assert bundle["run_lifecycle"]["terminal_event"] == "run_completed"


def test_runtime_support_bundle_promotes_redacted_provider_preflight() -> None:
    """Support bundle should expose route/preflight hints without secrets."""
    output = AgentRunOutput(
        run_id="run_obs_bundle_2",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.LLM_CALL_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_2",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
                options={
                    "payload": {
                        "route_profile": {
                            "profile_id": "openrouter:openrouter:openai__gpt-5.5",
                            "base_url_family": "openrouter",
                            "supports_forced_tool_choice": False,
                        },
                        "provider_preflight": {
                            "route_profile_id": (
                                "openrouter:openrouter:openai__gpt-5.5"
                            ),
                            "request_shape": {
                                "tool_choice_policy": (
                                    "forced_tool_choice_downgraded_to_auto"
                                ),
                                "api_key": "secret",
                            },
                            "redaction": {"safe_by_default": True},
                        },
                    },
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_2",
                    "attempt_id": "attempt_1",
                    "seq": 2,
                },
            ),
        ],
        metadata={"base_url": "https://openrouter.ai/api/v1?token=secret"},
    )

    bundle = build_runtime_support_bundle(output)

    assert bundle["route_profile"]["base_url_family"] == "openrouter"
    assert bundle["provider_preflight"]["request_shape"]["tool_choice_policy"] == (
        "forced_tool_choice_downgraded_to_auto"
    )
    assert bundle["provider_preflight"]["request_shape"]["api_key"] == "<redacted>"
    assert bundle["policy_evaluations"]["would_fire_policy_ids"] == [
        "provider_request_shape_preflight"
    ]
    assert bundle["policy_evaluations"]["selected_actions"] == ["reshape_request"]
    supervisor = bundle["run_supervisor_state"]
    assert supervisor["lifecycle_state"] == "completed"
    assert supervisor["heartbeat_status"] == "terminal"
    assert supervisor["fallback_counters"] == {"reshape_request": 1}
    assert bundle["metadata"]["base_url"] == "<redacted>"
    diagnostics = bundle["runtime_timeline"]["diagnostics"]
    assert diagnostics["timeline_row_count"] == 2
    assert diagnostics["provider_route_profile_id"] == (
        "openrouter:openrouter:openai__gpt-5.5"
    )
    assert bundle["run_lifecycle"]["reconnect_cursor"] == "run_obs_bundle_2:2"


def test_runtime_support_bundle_exposes_runtime_decisions() -> None:
    output = AgentRunOutput(
        run_id="run_obs_bundle_decisions",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUNTIME_DECISION,
                context={
                    "run_id": "run_obs_bundle_decisions",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
                options={
                    "payload": {
                        "decision_id": "dec_support",
                        "run_id": "run_obs_bundle_decisions",
                        "attempt_id": "attempt_1",
                        "seq": 1,
                        "kind": "steering",
                        "trigger": "control_applied",
                        "action": "continue",
                        "reason": "control_applied_at_step_boundary",
                        "status": "applied",
                    }
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_decisions",
                    "attempt_id": "attempt_1",
                    "seq": 2,
                },
            ),
        ],
    )

    bundle = build_runtime_support_bundle(output)

    assert bundle["runtime_decisions"]["count"] == 1
    assert bundle["policy_evaluations"]["observe_mode"] is True
    assert bundle["runtime_decisions"]["last_decision"]["reason"] == (
        "control_applied_at_step_boundary"
    )
    assert bundle["goal_state"]["status"] == "inactive"
    assert bundle["validation_gates"]["statuses"]["deterministic_tests"] == "not_run"


def test_runtime_support_bundle_exposes_validation_gate_artifacts() -> None:
    output = AgentRunOutput(
        run_id="run_obs_bundle_gates",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_gates",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
            )
        ],
        metadata={
            "validation_gates": [
                {
                    "gate_id": "deterministic_tests",
                    "status": "passed",
                    "command": "uv run pytest tests/runtime/test_policy_evaluator.py -q",
                    "evidence_path": "artifacts/policy-evaluator.txt",
                }
            ]
        },
    )

    bundle = build_runtime_support_bundle(output)

    gates = bundle["validation_gates"]
    assert gates["statuses"]["deterministic_tests"] == "passed"
    assert gates["statuses"]["phoenix_trace"] == "not_run"
    assert gates["gates"][0]["evidence_path"] == "artifacts/policy-evaluator.txt"


def test_runtime_support_bundle_exposes_capability_pack_resolution() -> None:
    output = AgentRunOutput(
        run_id="run_obs_bundle_pack",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_pack",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
            )
        ],
        metadata={
            "capability_pack_id": "deep_research_chat_demo",
            "capability_adapter_id": "chat_demo",
            "capability_scenario_ids": ["chat_demo.deep_research.source_report.v1"],
            "validation_gates": [
                {
                    "gate_id": "support_bundle_artifact",
                    "status": "passed",
                    "evidence_path": "artifacts/support-bundle.json",
                }
            ],
        },
    )

    bundle = build_runtime_support_bundle(output)

    resolution = bundle["capability_pack_resolution"]
    assert resolution["pack_id"] == "deep_research_chat_demo"
    assert resolution["adapter_id"] == "chat_demo"
    assert resolution["scenario_ids"] == ["chat_demo.deep_research.source_report.v1"]
    assert resolution["gate_statuses"]["support_bundle_artifact"] == "passed"
    assert resolution["gate_statuses"]["openrouter_live_preflight"] == "skipped"
    assert resolution["evidence_index"]["artifacts"][0]["artifact_type"] == (
        "skip_justification"
    )


def test_runtime_support_bundle_exposes_skill_lifecycle_projection() -> None:
    report = seed_chat_demo_skill_lifecycle_report()
    output = AgentRunOutput(
        run_id="run_obs_bundle_skills",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_bundle_skills",
                    "attempt_id": "attempt_1",
                    "seq": 1,
                },
            )
        ],
        metadata={
            "skill_lifecycle_report": report.model_dump(mode="json"),
        },
    )

    bundle = build_runtime_support_bundle(output)

    skills = bundle["skill_lifecycle"]
    assert skills["report_id"] == report.report_id
    assert skills["usage_summary"]["discovered"] == report.usage_summary.discovered
    assert skills["redaction"]["contains_raw_skill_body"] is False
    assert skills["selection_decisions"]
    assert "# " not in str(skills)


def test_persisted_support_bundle_redacts_event_payload_secrets() -> None:
    """Persisted replay bundle should redact sensitive payload fields."""
    persisted = {
        "run_id": "run_1",
        "event_count": 1,
        "trajectory": ["run_started"],
        "events": [{"type": "run_started", "payload": {"auth_token": "123"}}],
        "metadata": {"password": "x"},
    }
    bundle = build_persisted_support_bundle(persisted)
    assert bundle["events"][0]["payload"]["auth_token"] == "<redacted>"
    assert bundle["metadata"]["password"] == "<redacted>"
    assert bundle["runtime_timeline"]["diagnostics"]["durability"] == "persisted_replay"
    assert bundle["run_lifecycle"]["run_id"] == "run_1"
    assert bundle["run_lifecycle"]["reconnect_cursor"] is None
    assert bundle["run_supervisor_state"]["heartbeat_status"] == "active"


def test_persisted_support_bundle_rebuilds_capability_pack_resolution() -> None:
    persisted = {
        "run_id": "run_pack_replay",
        "event_count": 1,
        "events": [{"type": "run_completed", "payload": {}}],
        "metadata": {
            "capability_pack_id": "deep_research_chat_demo",
            "capability_adapter_id": "chat_demo",
            "capability_scenario_ids": ["chat_demo.deep_research.source_report.v1"],
        },
    }

    bundle = build_persisted_support_bundle(persisted)

    resolution = bundle["capability_pack_resolution"]
    assert resolution["pack_id"] == "deep_research_chat_demo"
    assert resolution["scenario_ids"] == ["chat_demo.deep_research.source_report.v1"]
    assert resolution["gate_statuses"]["openrouter_live_preflight"] == "skipped"
