"""Tests for span_attribute_resolver hook on optional exporters."""

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
    TraceExport,
    TraceSpan,
    build_trace_export,
)


def _payload(*, run_id: str = "run_resolver_1", extra_events: int = 2) -> TraceExport:
    events = [
        new_runtime_event(
            event_type=RuntimeEventType.RUN_COMPLETED,
            context={"run_id": run_id, "attempt_id": "attempt_1", "seq": i + 1},
        )
        for i in range(extra_events)
    ]
    output = AgentRunOutput(
        run_id=run_id,
        attempt_id="attempt_1",
        status=RunStatus.COMPLETED,
        terminal_reason=TerminalReason.FINAL_ANSWER,
        events=events,
    )
    return build_trace_export(output)


def test_no_resolver_keeps_existing_behavior_phoenix() -> None:
    """Without resolver Phoenix metadata stays minimal."""
    result = OpenTelemetryPhoenixTraceExporter().export(_payload())
    assert "custom_attribute_count" not in result.metadata
    assert "custom_attribute_spans" not in result.metadata
    assert "custom_attribute_resolver_errors" not in result.metadata


def test_no_resolver_keeps_existing_behavior_langfuse() -> None:
    """Without resolver Langfuse metadata stays minimal."""
    result = LangfuseTraceExporter().export(_payload())
    assert "custom_attribute_count" not in result.metadata


def test_resolver_attributes_counted_phoenix() -> None:
    """Resolver attributes are counted in Phoenix exporter metadata."""

    def resolver(span: TraceSpan, _payload: TraceExport) -> dict:
        return {
            "host.tenant_id": "tenant-123",
            "host.run_seq": span.seq,
            "host.is_terminal": span.event_type.endswith("_completed"),
            "host.usage_ratio": 0.85,
        }

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload()
    )
    assert result.metadata["custom_attribute_count"] == 8  # 2 spans * 4 attrs
    assert result.metadata["custom_attribute_spans"] == 2
    assert "custom_attribute_resolver_errors" not in result.metadata


def test_resolver_invalid_value_types_filtered() -> None:
    """Non-primitive values are silently dropped (e.g. list, dict, None)."""

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        return {
            "host.ok_str": "x",
            "host.ok_int": 1,
            "host.bad_list": [1, 2, 3],
            "host.bad_dict": {"nested": "value"},
            "host.bad_none": None,
            "host.bad_object": object(),
        }

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=1)
    )
    assert result.metadata["custom_attribute_count"] == 2
    assert result.metadata["custom_attribute_spans"] == 1


def test_resolver_non_string_keys_filtered() -> None:
    """Resolver keys that are not strings are silently dropped."""

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        return {
            "host.ok": "value",
            123: "bad_int_key",
            None: "bad_none_key",
        }

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=1)
    )
    assert result.metadata["custom_attribute_count"] == 1


def test_resolver_raising_exception_isolated() -> None:
    """A resolver that raises does not crash the export; error is recorded."""

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        raise RuntimeError("boom")

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=2)
    )
    assert result.metadata["custom_attribute_count"] == 0
    assert result.metadata["custom_attribute_spans"] == 0
    errors = result.metadata["custom_attribute_resolver_errors"]
    assert isinstance(errors, list)
    assert any("RuntimeError" in entry for entry in errors)


def test_resolver_returning_non_dict_isolated() -> None:
    """A resolver that returns the wrong type is treated as empty + reported."""

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        return ["bad", "return", "type"]  # type: ignore[return-value]

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=1)
    )
    assert result.metadata["custom_attribute_count"] == 0
    assert (
        "resolver_invalid_type" in result.metadata["custom_attribute_resolver_errors"]
    )


def test_resolver_errors_deduplicated() -> None:
    """Same error class across spans appears once in the error list."""

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        raise ValueError("repeat")

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=3)
    )
    errors = result.metadata["custom_attribute_resolver_errors"]
    assert errors == ["resolver_error:ValueError"]


def test_resolver_works_with_langfuse_exporter() -> None:
    """Same resolver shape works for Langfuse sink."""

    def resolver(span: TraceSpan, _payload: TraceExport) -> dict:
        return {"host.tenant_id": "tenant-x", "host.run_seq": span.seq}

    result = LangfuseTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=2)
    )
    assert result.sink == "langfuse_optional"
    assert result.metadata["custom_attribute_count"] == 4
    assert result.metadata["custom_attribute_spans"] == 2


def test_resolver_can_read_trace_export_metadata() -> None:
    """Resolver gets the full TraceExport, so it can read trace-level metadata."""

    def resolver(_span: TraceSpan, payload: TraceExport) -> dict:
        return {
            "host.trace_id": payload.trace_id,
            "host.run_id": payload.run_id,
        }

    payload = _payload(extra_events=1)
    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        payload
    )
    assert result.metadata["custom_attribute_count"] == 2


def test_dependency_unavailable_still_runs_resolver_for_diagnostics() -> None:
    """Resolver runs even when OTLP/Langfuse dependency is missing.

    This lets host applications verify their attribute pipeline in
    dependency-free CI before wiring the real SDK.
    """

    def resolver(_span: TraceSpan, _payload: TraceExport) -> dict:
        return {"host.tenant_id": "tenant-1"}

    result = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=resolver).export(
        _payload(extra_events=1)
    )
    # Could be either depending on the test environment; both must show counts.
    assert result.metadata["status"] in {"dependency_unavailable", "exported"}
    assert result.metadata["custom_attribute_count"] == 1
