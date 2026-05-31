"""Tests for enriched token_pressure WARNING event emission."""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.runtime.single_agent.llm_step import _emit_token_pressure_warning
from agent_driver.runtime.single_agent.types import EventSpec, RunContext


class _CaptureHost:
    """Minimal host stub that records all emitted EventSpec instances."""

    def __init__(self) -> None:
        self.events: list[EventSpec] = []

    def _emit(self, event: EventSpec) -> None:
        self.events.append(event)


def _make_context(token_pressure: dict[str, Any] | None) -> RunContext:
    run_input = AgentRunInput(
        input="hello",
        run_id="run_test_1",
        agent_id="agent",
        graph_preset="single_react",
    )
    metadata: dict[str, Any] = {}
    if token_pressure is not None:
        metadata["token_pressure"] = token_pressure
    return RunContext(
        run_input=run_input,
        identifiers={"run_id": "run_test_1", "attempt_id": "att_test_1"},
        metadata=metadata,
    )


def test_emission_skipped_for_ok_state() -> None:
    """No warning event is emitted when state is ok."""
    host = _CaptureHost()
    context = _make_context({"state": "ok"})
    _emit_token_pressure_warning(host, context)
    assert not host.events


def test_emission_skipped_when_token_pressure_missing() -> None:
    """No warning emitted when token_pressure metadata absent."""
    host = _CaptureHost()
    context = _make_context(None)
    _emit_token_pressure_warning(host, context)
    assert not host.events


def test_warning_payload_carries_signal_id_severity_and_thresholds() -> None:
    """Emission packs stable signal_id, severity, all thresholds and ratio."""
    host = _CaptureHost()
    context = _make_context(
        {
            "state": "warning",
            "used_tokens_estimate": 8000,
            "remaining_tokens_estimate": 2500,
            "context_window_estimate": 12000,
            "output_token_reserve": 1500,
            "warning_threshold": 7500,
            "compact_threshold": 9000,
            "blocking_threshold": 10500,
        }
    )
    _emit_token_pressure_warning(host, context)
    assert len(host.events) == 1
    event = host.events[0]
    assert event.event_type is RuntimeEventType.WARNING
    payload = event.payload or {}
    assert payload["kind"] == "token_pressure"
    assert payload["signal_id"] == "context_above_soft_threshold"
    assert payload["severity"] == "warning"
    assert payload["state"] == "warning"
    assert payload["used_tokens_estimate"] == 8000
    assert payload["remaining_tokens_estimate"] == 2500
    assert payload["context_window_estimate"] == 12000
    assert payload["output_token_reserve"] == 1500
    assert payload["warning_threshold"] == 7500
    assert payload["compact_threshold"] == 9000
    assert payload["blocking_threshold"] == 10500
    assert payload["context_usage_ratio"] == pytest.approx(0.6667, abs=0.0001)
    assert payload["usage_ratio"] == pytest.approx(0.6667, abs=0.0001)


def test_warning_payload_prefers_snapshot_context_usage_ratio() -> None:
    """Emission keeps the estimator's rounded ratio when present."""
    host = _CaptureHost()
    context = _make_context(
        {
            "state": "warning",
            "used_tokens_estimate": 8000,
            "context_window_estimate": 12000,
            "context_usage_ratio": 0.42,
        }
    )
    _emit_token_pressure_warning(host, context)
    payload = host.events[0].payload or {}
    assert payload["context_usage_ratio"] == 0.42
    assert payload["usage_ratio"] == 0.42


def test_compact_recommended_state_has_distinct_signal_id() -> None:
    """state=compact_recommended maps to context_compact_recommended."""
    host = _CaptureHost()
    context = _make_context(
        {
            "state": "compact_recommended",
            "used_tokens_estimate": 9200,
            "context_window_estimate": 12000,
            "warning_threshold": 7500,
            "compact_threshold": 9000,
            "blocking_threshold": 10500,
        }
    )
    _emit_token_pressure_warning(host, context)
    assert len(host.events) == 1
    payload = host.events[0].payload or {}
    assert payload["signal_id"] == "context_compact_recommended"
    assert payload["severity"] == "warning"
    assert payload["state"] == "compact_recommended"


def test_blocking_state_carries_critical_severity() -> None:
    """state=blocking maps to context_blocking_threshold with critical severity."""
    host = _CaptureHost()
    context = _make_context(
        {
            "state": "blocking",
            "used_tokens_estimate": 11000,
            "context_window_estimate": 12000,
            "blocking_threshold": 10500,
        }
    )
    _emit_token_pressure_warning(host, context)
    assert len(host.events) == 1
    payload = host.events[0].payload or {}
    assert payload["signal_id"] == "context_blocking_threshold"
    assert payload["severity"] == "critical"
    assert payload["state"] == "blocking"
    assert payload["context_usage_ratio"] == pytest.approx(0.9167, abs=0.0001)
    assert payload["usage_ratio"] == pytest.approx(0.9167, abs=0.0001)


def test_context_usage_ratio_is_none_when_window_zero() -> None:
    """Division-by-zero guard: ratio is None when context_window_estimate is 0."""
    host = _CaptureHost()
    context = _make_context(
        {
            "state": "warning",
            "used_tokens_estimate": 100,
            "context_window_estimate": 0,
        }
    )
    _emit_token_pressure_warning(host, context)
    assert len(host.events) == 1
    payload = host.events[0].payload or {}
    assert payload["context_usage_ratio"] is None
    assert payload["usage_ratio"] is None
