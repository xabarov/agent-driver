"""EPIC-01 Work Package C — backends and legacy-seam adapters."""

import pytest

import agent_driver.execution as ex
from agent_driver.tools.context import tool_call_context_scope


def _id(backend_id="b"):
    return ex.ExecutionIdentity(
        backend_id=backend_id,
        run_id="r1",
        attempt_id="a1",
        tool_call_id="t1",
        request_id="q1",
    )


# --------------------------------------------------------------------------- #
# LocalExecutionBackend
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_local_run_command_completed():
    be = ex.LocalExecutionBackend()
    req = ex.ExecutionCommandRequest(
        identity=_id("local"),
        command="printf hello",
        cwd="/",
        timeout_seconds=10,
        max_output_chars=6000,
    )
    res = await be.run_command(req)
    assert res.terminal_state is ex.ExecutionTerminalState.COMPLETED
    assert res.exit_code == 0
    assert res.stdout == "hello"
    assert res.timed_out is False


@pytest.mark.asyncio
async def test_local_run_command_nonzero_exit_is_still_completed():
    be = ex.LocalExecutionBackend()
    req = ex.ExecutionCommandRequest(
        identity=_id("local"),
        command="exit 3",
        cwd="/",
        timeout_seconds=10,
        max_output_chars=6000,
    )
    res = await be.run_command(req)
    assert res.terminal_state is ex.ExecutionTerminalState.COMPLETED
    assert res.exit_code == 3


@pytest.mark.asyncio
async def test_local_run_command_timeout():
    be = ex.LocalExecutionBackend()
    req = ex.ExecutionCommandRequest(
        identity=_id("local"),
        command="sleep 5",
        cwd="/",
        timeout_seconds=0.2,
        max_output_chars=6000,
    )
    res = await be.run_command(req)
    assert res.timed_out is True
    assert res.terminal_state is ex.ExecutionTerminalState.TIMED_OUT


@pytest.mark.asyncio
async def test_local_read_write_round_trip(tmp_path):
    be = ex.LocalExecutionBackend()
    p = tmp_path / "f.txt"
    wres = await be.write_text(
        ex.ExecutionWriteRequest(identity=_id("local"), path=str(p), content="héllo")
    )
    assert wres.bytes_written == len("héllo".encode())
    rres = await be.read_text(
        ex.ExecutionReadRequest(identity=_id("local"), path=str(p), max_bytes=10_000)
    )
    assert rres.content == "héllo"
    assert rres.size_bytes == len("héllo".encode())


@pytest.mark.asyncio
async def test_local_read_exceeds_max_bytes(tmp_path):
    be = ex.LocalExecutionBackend()
    p = tmp_path / "big.txt"
    p.write_text("x" * 100, encoding="utf-8")
    with pytest.raises(ex.OutputLimitExceededError):
        await be.read_text(
            ex.ExecutionReadRequest(identity=_id("local"), path=str(p), max_bytes=10)
        )


# --------------------------------------------------------------------------- #
# FakeExecutionBackend
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_fake_scripted_and_default_and_records():
    be = ex.FakeExecutionBackend(
        commands={"ls": ex.CommandOutcome(stdout="a\nb", exit_code=0)},
        default_outcome=ex.CommandOutcome(stderr="nope", exit_code=127),
    )
    ls = await be.run_command(
        ex.ExecutionCommandRequest(
            identity=_id(),
            command="ls",
            cwd="/",
            timeout_seconds=1,
            max_output_chars=10,
        )
    )
    assert ls.stdout == "a\nb" and ls.exit_code == 0
    other = await be.run_command(
        ex.ExecutionCommandRequest(
            identity=_id(),
            command="boom",
            cwd="/",
            timeout_seconds=1,
            max_output_chars=10,
        )
    )
    assert other.exit_code == 127 and other.stderr == "nope"
    assert [c.command for c in be.command_calls] == ["ls", "boom"]


@pytest.mark.asyncio
async def test_fake_timed_out_result_and_raised_timeout():
    be = ex.FakeExecutionBackend(
        commands={"slow": ex.CommandOutcome(timed_out=True)},
        raise_timeout_for={"dead"},
    )
    slow = await be.run_command(
        ex.ExecutionCommandRequest(
            identity=_id(),
            command="slow",
            cwd="/",
            timeout_seconds=1,
            max_output_chars=10,
        )
    )
    assert slow.terminal_state is ex.ExecutionTerminalState.TIMED_OUT
    with pytest.raises(ex.ExecutionTimeoutError):
        await be.run_command(
            ex.ExecutionCommandRequest(
                identity=_id(),
                command="dead",
                cwd="/",
                timeout_seconds=1,
                max_output_chars=10,
            )
        )


@pytest.mark.asyncio
async def test_fake_file_io():
    be = ex.FakeExecutionBackend(files={"/a": "seed"})
    r = await be.read_text(
        ex.ExecutionReadRequest(identity=_id(), path="/a", max_bytes=100)
    )
    assert r.content == "seed"
    await be.write_text(
        ex.ExecutionWriteRequest(identity=_id(), path="/b", content="new")
    )
    assert be.files["/b"] == "new"
    with pytest.raises(FileNotFoundError):
        await be.read_text(
            ex.ExecutionReadRequest(identity=_id(), path="/missing", max_bytes=100)
        )


# --------------------------------------------------------------------------- #
# CompositeExecutionBackend
# --------------------------------------------------------------------------- #
class _LegacyRunner:
    async def run_command(self, command, *, cwd, timeout_seconds):
        return {"stdout": "out", "stderr": "", "timed_out": False, "exit_code": 0}


class _LegacyFileIO:
    def __init__(self):
        self.store = {}

    async def read_text(self, path):
        return self.store.get(path, "")

    async def write_text(self, path, content):
        self.store[path] = content


@pytest.mark.asyncio
async def test_composite_maps_legacy_runner_and_fileio():
    fio = _LegacyFileIO()
    be = ex.CompositeExecutionBackend(command_runner=_LegacyRunner(), file_io=fio)
    res = await be.run_command(
        ex.ExecutionCommandRequest(
            identity=_id(), command="x", cwd="/", timeout_seconds=1, max_output_chars=5
        )
    )
    assert (
        res.stdout == "out"
        and res.terminal_state is ex.ExecutionTerminalState.COMPLETED
    )
    await be.write_text(
        ex.ExecutionWriteRequest(identity=_id(), path="/p", content="z")
    )
    assert fio.store["/p"] == "z"


@pytest.mark.asyncio
async def test_composite_missing_capability_raises_unsupported():
    be = ex.CompositeExecutionBackend(command_runner=None, file_io=None)
    with pytest.raises(ex.UnsupportedCapabilityError):
        await be.run_command(
            ex.ExecutionCommandRequest(
                identity=_id(),
                command="x",
                cwd="/",
                timeout_seconds=1,
                max_output_chars=5,
            )
        )
    with pytest.raises(ex.UnsupportedCapabilityError):
        await be.read_text(
            ex.ExecutionReadRequest(identity=_id(), path="/a", max_bytes=10)
        )


@pytest.mark.asyncio
async def test_composite_malformed_runner_result_raises_protocol_error():
    class Bad:
        async def run_command(self, command, *, cwd, timeout_seconds):
            return "not a dict"

    be = ex.CompositeExecutionBackend(command_runner=Bad())
    with pytest.raises(ex.BackendProtocolError):
        await be.run_command(
            ex.ExecutionCommandRequest(
                identity=_id(),
                command="x",
                cwd="/",
                timeout_seconds=1,
                max_output_chars=5,
            )
        )


# --------------------------------------------------------------------------- #
# Adapters: backend -> legacy run-scoped seams
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_backend_command_runner_returns_legacy_dict_shape():
    be = ex.FakeExecutionBackend(
        commands={"c": ex.CommandOutcome(stdout="hi", exit_code=2)}
    )
    runner = ex.BackendCommandRunner(be)
    out = await runner.run_command("c", cwd="/w", timeout_seconds=3)
    assert out == {"stdout": "hi", "stderr": "", "timed_out": False, "exit_code": 2}
    assert be.command_calls[0].cwd == "/w"


@pytest.mark.asyncio
async def test_backend_file_io_round_trips_through_backend():
    be = ex.FakeExecutionBackend()
    fio = ex.BackendFileIO(be)
    await fio.write_text("/x", "data")
    assert be.files["/x"] == "data"
    assert await fio.read_text("/x") == "data"


def test_identity_from_context_reads_scope_and_falls_back():
    # No context set -> placeholders, contract invariants still hold.
    ident = ex.identity_from_context("local")
    assert ident.backend_id == "local"
    assert ident.run_id == "unknown-run"
    assert ident.tool_call_id == "unbound"
    assert ident.request_id  # non-empty synthesized key
    # With run context set, the real run_id is picked up.
    with tool_call_context_scope(run_id="R", thread_id="T"):
        ident2 = ex.identity_from_context("local")
        assert ident2.run_id == "R"
