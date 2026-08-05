"""EPIC-01 execution contracts — round-trip, JSON safety, invariants, typed errors."""

import pytest

import agent_driver.execution as ex


def _identity():
    return ex.ExecutionIdentity(
        backend_id="local", run_id="r1", attempt_id="a1",
        tool_call_id="t1", request_id="q1",
    )


def test_command_result_round_trips_through_json():
    r = ex.ExecutionCommandResult(
        identity=_identity(),
        terminal_state=ex.ExecutionTerminalState.COMPLETED,
        exit_code=0,
        stdout="hello",
        stderr="",
        bounds=ex.ExecutionBounds(max_output_chars=6000),
    )
    back = ex.ExecutionCommandResult.model_validate_json(r.model_dump_json())
    assert back == r
    assert back.identity.tool_call_id == "t1"


def test_timed_out_forces_terminal_state():
    r = ex.ExecutionCommandResult(
        identity=_identity(),
        terminal_state=ex.ExecutionTerminalState.COMPLETED,  # inconsistent on purpose
        exit_code=1,
        timed_out=True,
        bounds=ex.ExecutionBounds(max_output_chars=10),
    )
    assert r.terminal_state is ex.ExecutionTerminalState.TIMED_OUT


def test_extra_fields_forbidden():
    with pytest.raises(Exception):
        ex.ExecutionIdentity(
            backend_id="local", run_id="r1", attempt_id="a1",
            tool_call_id="t1", request_id="q1", bogus="x",
        )


def test_identity_fields_required_non_empty():
    with pytest.raises(Exception):
        ex.ExecutionIdentity(
            backend_id="", run_id="r1", attempt_id="a1",
            tool_call_id="t1", request_id="q1",
        )


def test_read_write_contracts():
    rr = ex.ExecutionReadResult(
        identity=_identity(), path="/w/a.txt", content="x", size_bytes=1
    )
    assert ex.ExecutionReadResult.model_validate_json(rr.model_dump_json()) == rr
    wr = ex.ExecutionWriteResult(identity=_identity(), path="/w/a.txt", bytes_written=3)
    assert wr.bytes_written == 3


def test_capability_snapshot_defaults_unknown_and_versioned():
    snap = ex.CapabilitySnapshot(backend_id="local")
    assert snap.schema_version == ex.EXECUTION_SCHEMA_VERSION
    # missing evidence is UNKNOWN, never SUPPORTED
    assert snap.command is ex.CapabilityState.UNKNOWN
    assert snap.file_read is ex.CapabilityState.UNKNOWN


def test_capability_snapshot_metadata_bounded():
    with pytest.raises(ValueError):
        ex.CapabilitySnapshot(backend_id="local", metadata={"x": object()})


def test_typed_errors_are_categorizable_by_code_and_type():
    for cls, code in [
        (ex.UnsupportedCapabilityError, "unsupported_capability"),
        (ex.ExecutionTimeoutError, "execution_timeout"),
        (ex.ExecutionTransportError, "execution_transport"),
        (ex.IndeterminateExecutionError, "indeterminate_execution"),
        (ex.OutputLimitExceededError, "output_limit_exceeded"),
        (ex.BackendProtocolError, "backend_protocol_violation"),
    ]:
        err = cls("boom")
        assert err.code == code
        assert isinstance(err, ex.ExecutionError)


def test_error_message_is_bounded():
    err = ex.ExecutionError("x" * 5000)
    assert len(err.message) <= 500


def test_json_schema_generation_smoke():
    for model in (
        ex.ExecutionCommandRequest, ex.ExecutionCommandResult,
        ex.ExecutionReadResult, ex.ExecutionWriteResult, ex.CapabilitySnapshot,
    ):
        schema = model.model_json_schema()
        assert schema["type"] == "object"


def test_backend_protocol_is_runtime_checkable():
    class Good:
        backend_id = "x"
        async def run_command(self, request): ...
        async def read_text(self, request): ...
        async def write_text(self, request): ...

    assert isinstance(Good(), ex.ExecutionBackend)
    assert not isinstance(object(), ex.ExecutionBackend)
