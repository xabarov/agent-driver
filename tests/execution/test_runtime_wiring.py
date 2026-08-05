"""EPIC-01 Work Package D — the built-in tools route through an injected backend.

These drive the REAL bash handler and the REAL routed file-IO helpers (the exact
seams the read/write/edit tools use) with the backend scopes installed exactly as
``Runner._drive_steps`` installs them. Nothing in the tools changed; only the
byte transfer is redirected, and the enriched identity reaches the backend.
"""

import pytest

import agent_driver.execution as ex
from agent_driver.execution.adapters import BackendCommandRunner, BackendFileIO
from agent_driver.tools.builtin.filesystem._paths import (
    read_text_routed,
    write_text_routed,
)
from agent_driver.tools.builtin.shell import _bash_handler
from agent_driver.tools.context import (
    command_runner_scope,
    fs_io_scope,
    tool_call_context_scope,
    workspace_cwd_scope,
)


@pytest.mark.asyncio
async def test_bash_handler_routes_through_backend_with_enriched_identity(tmp_path):
    fake = ex.FakeExecutionBackend(
        commands={"echo hi": ex.CommandOutcome(stdout="ROUTED", exit_code=0)}
    )
    with (
        workspace_cwd_scope(tmp_path),
        command_runner_scope(BackendCommandRunner(fake)),
        tool_call_context_scope(
            run_id="R", thread_id="T", tool_call_id="CALL7", attempt_id="A1"
        ),
    ):
        result = await _bash_handler({"command": "echo hi"})

    assert result["stdout"] == "ROUTED"
    assert result["exit_code"] == 0
    # the tool's command reached the backend...
    assert [c.command for c in fake.command_calls] == ["echo hi"]
    # ...carrying the executor-enriched identity (tool_call_id -> request_id).
    ident = fake.command_calls[0].identity
    assert ident.run_id == "R"
    assert ident.tool_call_id == "CALL7"
    assert ident.attempt_id == "A1"
    assert ident.request_id == "CALL7"


@pytest.mark.asyncio
async def test_bash_handler_default_runs_locally(tmp_path):
    # No backend scope -> unchanged local subprocess behavior.
    with workspace_cwd_scope(tmp_path):
        result = await _bash_handler({"command": "echo ok"})
    assert result["stdout"].strip() == "ok"
    assert result["exit_code"] == 0


@pytest.mark.asyncio
async def test_routed_file_io_goes_through_backend_not_disk(tmp_path):
    fake = ex.FakeExecutionBackend()
    target = tmp_path / "routed.txt"
    with (
        fs_io_scope(BackendFileIO(fake)),
        tool_call_context_scope(run_id="R", tool_call_id="W1"),
    ):
        await write_text_routed(target, "hello-routed")
        # write landed in the backend, NOT on local disk
        assert fake.files[str(target)] == "hello-routed"
        assert not target.exists()
        # and the read seam reads it back from the backend
        content = await read_text_routed(target, max_bytes=10_000)
    assert content == "hello-routed"
    assert fake.write_calls[0].identity.tool_call_id == "W1"


@pytest.mark.asyncio
async def test_routed_read_still_enforces_size_guard(tmp_path):
    # The tool's post-read size guard is unchanged even when routed.
    fake = ex.FakeExecutionBackend(files={str(tmp_path / "big.txt"): "x" * 100})
    with fs_io_scope(BackendFileIO(fake)):
        with pytest.raises(ValueError):
            await read_text_routed(tmp_path / "big.txt", max_bytes=10)
