"""Tests for reusable Phoenix/OpenTelemetry helpers."""

from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.observability.phoenix import (
    PhoenixTracingConfig,
    _reset_phoenix_tracing_for_tests,
    agent_run_otel_attributes,
    normalize_phoenix_http_endpoint,
    phoenix_tracing_status,
    runtime_event_otel_attributes,
    safe_json,
    setup_phoenix_tracing,
)


def test_normalize_phoenix_http_endpoint() -> None:
    assert normalize_phoenix_http_endpoint("http://localhost:6006") == (
        "http://localhost:6006/v1/traces"
    )
    assert normalize_phoenix_http_endpoint("http://localhost:6006/v1/traces") == (
        "http://localhost:6006/v1/traces"
    )


def test_setup_phoenix_tracing_uses_optional_register(monkeypatch) -> None:
    _reset_phoenix_tracing_for_tests()
    calls: list[dict[str, object]] = []
    phoenix_module = ModuleType("phoenix")
    otel_module = ModuleType("phoenix.otel")

    def register(**kwargs: object) -> None:
        calls.append(kwargs)

    otel_module.register = register  # type: ignore[attr-defined]
    phoenix_module.otel = otel_module  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "phoenix", phoenix_module)
    monkeypatch.setitem(sys.modules, "phoenix.otel", otel_module)

    status = setup_phoenix_tracing(
        PhoenixTracingConfig(
            enabled=True,
            project_name="agent-driver-test",
            collector_endpoint="http://phoenix:6006",
        )
    )

    assert status == {
        "enabled": True,
        "configured": True,
        "project_name": "agent-driver-test",
        "endpoint": "http://phoenix:6006/v1/traces",
        "error": None,
    }
    assert calls == [
        {
            "project_name": "agent-driver-test",
            "auto_instrument": False,
            "batch": False,
            "endpoint": "http://phoenix:6006/v1/traces",
            "protocol": "http/protobuf",
        }
    ]


def test_setup_phoenix_tracing_disabled_is_noop() -> None:
    _reset_phoenix_tracing_for_tests()
    status = setup_phoenix_tracing(PhoenixTracingConfig(enabled=False))

    assert status == {
        "enabled": False,
        "configured": False,
        "project_name": None,
        "endpoint": None,
        "error": None,
    }
    assert phoenix_tracing_status() == status


def test_safe_json_truncates_and_stringifies() -> None:
    full = safe_json({"value": SimpleNamespace(name="demo")}, max_chars=120)
    text = safe_json({"value": SimpleNamespace(name="demo")}, max_chars=16)

    assert "demo" in full
    assert text.endswith("...")


def test_agent_run_otel_attributes_include_policy_and_app_metadata() -> None:
    attrs = agent_run_otel_attributes(
        AgentRunInput(
            input="hello",
            run_id="run_1",
            thread_id="thread_1",
            agent_id="agent_1",
            graph_preset="single_agent",
            tool_policy=ToolPolicyInput(
                metadata={
                    "planning_hint": {"level": "force"},
                    "task_contract": {
                        "kind": "deliverable",
                        "requires_research": True,
                    },
                }
            ),
            app_metadata={"session_id": "session_1", "chat_mode": True},
        ),
        app_metadata_attributes={
            "session_id": "chat.session_id",
            "chat_mode": "chat.mode",
        },
    )

    assert attrs["agent.run_id"] == "run_1"
    assert attrs["chat.session_id"] == "session_1"
    assert attrs["chat.mode"] is True
    assert attrs["task_contract.kind"] == "deliverable"
    assert attrs["task_contract.requires_research"] is True
    assert "force" in str(attrs["planning.hint"])


def test_runtime_event_otel_attributes_compacts_runtime_data() -> None:
    attrs = runtime_event_otel_attributes(
        "tool_call_completed",
        {
            "tools": [
                {"tool_name": "web_search", "status": "done"},
                {"tool_name": "web_fetch", "status": "failed"},
            ],
            "planned_tool_calls": [{"tool_name": "update_plan"}],
            "usage": {"input_tokens": 10, "output_tokens": 20, "total_tokens": 30},
            "planning_snapshot": {
                "completed": 1,
                "total": 3,
                "in_progress_id": "step-2",
            },
            "force_final_reason": "runtime_guardrail",
            "continuation_reason": "text_form_tool_call",
            "tool_choice_effective": {"type": "tool", "name": "web_search"},
        },
    )

    assert attrs is not None
    assert attrs["runtime.event"] == "tool_call_completed"
    assert attrs["tool.names"] == "web_search,web_fetch"
    assert attrs["tool.statuses"] == "done,failed"
    assert attrs["llm.planned_tool_names"] == "update_plan"
    assert attrs["llm.usage.total_tokens"] == 30
    assert attrs["planning.completed"] == 1
    assert attrs["planning.in_progress_id"] == "step-2"
    assert attrs["force_final_reason"] == "runtime_guardrail"
    assert attrs["continuation_reason"] == "text_form_tool_call"
    assert "web_search" in str(attrs["tool_choice.effective"])
    assert runtime_event_otel_attributes("token_delta", {"text": "x"}) is None


def test_runtime_event_otel_attributes_includes_compaction_tags() -> None:
    attrs = runtime_event_otel_attributes(
        "memory_compacted",
        {
            "compaction_id": "cmp_1",
            "mode": "partial",
            "outcome": "success",
            "summarized_message_count": 6,
            "compaction_state": {"circuit_breaker_open": False},
        },
    )

    assert attrs is not None
    assert attrs["compaction.id"] == "cmp_1"
    assert attrs["compaction.mode"] == "partial"
    assert attrs["compaction.outcome"] == "success"
    assert attrs["compaction.summarized_message_count"] == 6
    assert attrs["compaction.circuit_breaker_open"] is False
