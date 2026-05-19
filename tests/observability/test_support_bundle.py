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
                context={"run_id": "run_obs_bundle_1", "attempt_id": "attempt_1", "seq": 1},
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
