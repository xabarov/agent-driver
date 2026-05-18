"""Observability trace export tests."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentRunOutput,
    CheckpointRef,
    RunStatus,
    RuntimeEventType,
    ToolRisk,
    ToolTrace,
    ToolTraceStatus,
    UsageSummary,
    new_runtime_event,
)
from agent_driver.observability import (
    LocalTraceExporter,
    NoOpTraceExporter,
    build_trace_export,
)


def _sample_output() -> AgentRunOutput:
    return AgentRunOutput(
        run_id="run_obs_1",
        attempt_id="attempt_obs_1",
        status=RunStatus.COMPLETED,
        terminal_reason="final_answer",
        events=[
            new_runtime_event(
                event_type=RuntimeEventType.RUN_STARTED,
                context={
                    "run_id": "run_obs_1",
                    "attempt_id": "attempt_obs_1",
                    "seq": 1,
                },
            ),
            new_runtime_event(
                event_type=RuntimeEventType.RUN_COMPLETED,
                context={
                    "run_id": "run_obs_1",
                    "attempt_id": "attempt_obs_1",
                    "seq": 2,
                },
                options={"payload": {"reason": "done"}},
            ),
        ],
        tool_trace=[
            ToolTrace(
                step=0,
                tool_name="lookup",
                tool_call_id="call_1",
                status=ToolTraceStatus.COMPLETED,
                args_summary={"query": "x"},
                risk=ToolRisk.LOW,
                side_effect="read_only",
                approval_mode="never",
            )
        ],
        usage=UsageSummary(input_tokens=10, output_tokens=5),
        checkpoint=CheckpointRef(
            checkpoint_id="cp_1",
            run_id="run_obs_1",
            attempt_id="attempt_obs_1",
            graph_id="single_react",
            created_at="2026-05-18T10:00:00Z",
            state_version="v1",
            storage_backend="memory",
        ),
        metadata={"source": "test"},
    )


def test_build_trace_export_is_deterministic() -> None:
    """Builder should produce stable trace id and ordered spans."""
    first = build_trace_export(_sample_output())
    second = build_trace_export(_sample_output())
    assert first.trace_id == second.trace_id
    assert [span.seq for span in first.spans] == [1, 2]
    assert first.metadata["status"] == "completed"


def test_noop_exporter_returns_sink_metadata() -> None:
    """No-op exporter should report span count without persistence."""
    payload = build_trace_export(_sample_output())
    result = NoOpTraceExporter().export(payload)
    assert result.sink == "noop"
    assert result.trace_id == payload.trace_id
    assert result.span_count == len(payload.spans)


def test_local_exporter_stores_and_snapshots() -> None:
    """Local exporter should persist trace payload in memory."""
    payload = build_trace_export(_sample_output())
    exporter = LocalTraceExporter()
    result = exporter.export(payload)
    assert result.sink == "local_memory"
    restored = exporter.get(payload.trace_id)
    assert restored is not None
    assert restored.run_id == "run_obs_1"
    snapshot = exporter.snapshot()
    assert snapshot[payload.trace_id]["span_count"] == len(payload.spans)
