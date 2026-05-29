"""Optional exporter behavior tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    RunStatus,
    RuntimeEventType,
    TerminalReason,
    new_runtime_event,
)
from agent_driver.observability import (
    LangfuseTraceExporter,
    OpenTelemetryPhoenixTraceExporter,
    build_trace_export,
)


def _payload():
    output = AgentRunOutput(
        run_id="run_opt_1",
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={"run_id": "run_opt_1", "attempt_id": "attempt_1", "seq": 1},
            )
        ],
    )
    return build_trace_export(output)


def test_optional_phoenix_exporter_graceful_without_dependency() -> None:
    """Exporter should return dependency_unavailable metadata when package missing."""
    result = OpenTelemetryPhoenixTraceExporter().export(_payload())
    assert result.sink == "phoenix_optional"
    assert result.metadata["status"] in {"dependency_unavailable", "exported"}


def test_optional_langfuse_exporter_graceful_without_dependency() -> None:
    """Exporter should return dependency_unavailable metadata when package missing."""
    result = LangfuseTraceExporter().export(_payload())
    assert result.sink == "langfuse_optional"
    assert result.metadata["status"] in {"dependency_unavailable", "exported"}
