"""Tests for warning event projection helper."""

from __future__ import annotations

from agent_driver.adapters import project_warning_event
from agent_driver.contracts import RunStreamEvent


def _make_event(*, event: str, data: dict) -> RunStreamEvent:
    return RunStreamEvent(
        schema_version="1.0",
        stream_id="run_1:1",
        run_id="run_1",
        attempt_id="att_1",
        seq=1,
        event=event,
        source="runtime_event",
        data=data,
        runtime_event_id="evt_1",
        created_at="2026-05-21T00:00:00Z",
    )


def test_non_warning_event_returns_none() -> None:
    """Projector should ignore non-warning events."""
    event = _make_event(event="token_delta", data={"delta_text": "hi"})
    assert project_warning_event(event) is None


def test_warning_with_unknown_kind_returns_none() -> None:
    """Forward-compatible degrade for unknown warning kinds."""
    event = _make_event(
        event="warning",
        data={"kind": "unknown_kind", "signal_id": "x", "severity": "info"},
    )
    assert project_warning_event(event) is None


def test_warning_missing_signal_id_returns_none() -> None:
    """Malformed payload (missing signal_id) is rejected."""
    event = _make_event(
        event="warning",
        data={"kind": "token_pressure", "severity": "warning"},
    )
    assert project_warning_event(event) is None


def test_warning_missing_severity_returns_none() -> None:
    """Malformed payload (missing severity) is rejected."""
    event = _make_event(
        event="warning",
        data={"kind": "token_pressure", "signal_id": "x"},
    )
    assert project_warning_event(event) is None


def test_token_pressure_warning_state_projection() -> None:
    """state=warning projects to context_above_soft_threshold + warning severity."""
    event = _make_event(
        event="warning",
        data={
            "kind": "token_pressure",
            "signal_id": "context_above_soft_threshold",
            "severity": "warning",
            "state": "warning",
            "used_tokens_estimate": 8000,
            "remaining_tokens_estimate": 2500,
            "context_window_estimate": 12000,
            "output_token_reserve": 1500,
            "warning_threshold": 7500,
            "compact_threshold": 9000,
            "blocking_threshold": 10500,
            "usage_ratio": 0.6667,
        },
    )
    projection = project_warning_event(event)
    assert projection is not None
    assert projection["kind"] == "token_pressure"
    assert projection["signal_id"] == "context_above_soft_threshold"
    assert projection["severity"] == "warning"
    data = projection["data"]
    assert data["state"] == "warning"
    assert data["used_tokens_estimate"] == 8000
    assert data["context_window_estimate"] == 12000
    assert data["warning_threshold"] == 7500
    assert data["compact_threshold"] == 9000
    assert data["blocking_threshold"] == 10500
    assert data["usage_ratio"] == 0.6667


def test_token_pressure_blocking_state_severity_critical() -> None:
    """state=blocking carries critical severity."""
    event = _make_event(
        event="warning",
        data={
            "kind": "token_pressure",
            "signal_id": "context_blocking_threshold",
            "severity": "critical",
            "state": "blocking",
            "used_tokens_estimate": 11000,
            "context_window_estimate": 12000,
            "blocking_threshold": 10500,
            "usage_ratio": 0.9167,
        },
    )
    projection = project_warning_event(event)
    assert projection is not None
    assert projection["signal_id"] == "context_blocking_threshold"
    assert projection["severity"] == "critical"
    assert projection["data"]["state"] == "blocking"


def test_token_pressure_compact_recommended_severity_warning() -> None:
    """state=compact_recommended carries warning severity, distinct signal."""
    event = _make_event(
        event="warning",
        data={
            "kind": "token_pressure",
            "signal_id": "context_compact_recommended",
            "severity": "warning",
            "state": "compact_recommended",
            "used_tokens_estimate": 9200,
            "context_window_estimate": 12000,
            "compact_threshold": 9000,
            "blocking_threshold": 10500,
            "usage_ratio": 0.7667,
        },
    )
    projection = project_warning_event(event)
    assert projection is not None
    assert projection["signal_id"] == "context_compact_recommended"
    assert projection["severity"] == "warning"
    assert projection["data"]["state"] == "compact_recommended"


def test_projection_omits_missing_fields() -> None:
    """Optional fields not in payload should not appear in projection.data."""
    event = _make_event(
        event="warning",
        data={
            "kind": "token_pressure",
            "signal_id": "context_above_soft_threshold",
            "severity": "warning",
            "state": "warning",
            # only the bare minimum, no thresholds at all
        },
    )
    projection = project_warning_event(event)
    assert projection is not None
    assert "warning_threshold" not in projection["data"]
    assert "compact_threshold" not in projection["data"]
    assert "usage_ratio" not in projection["data"]
