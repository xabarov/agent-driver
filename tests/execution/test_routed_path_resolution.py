"""EPIC-03 WP-C (slice 2) — routing-aware filesystem path resolution.

When an execution backend is routing bytes, the built-in read/write HANDLERS
must resolve and validate paths against the backend workspace contract WITHOUT
touching the local disk (no local stat, no local fallback), and reject traversal
escapes. Default local runs keep byte-for-byte the old behavior (covered by the
existing filesystem tool suite).
"""

from pathlib import Path

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution_lease import (
    ExecutionLease,
    ExecutionLeaseRef,
    LeaseState,
    WorkspacePaths,
)
from agent_driver.execution.adapters import BackendFileIO
from agent_driver.tools.builtin.filesystem.read import read_file_handler
from agent_driver.tools.builtin.filesystem.write import file_write_handler
from agent_driver.tools.context import (
    execution_lease_scope,
    fs_io_scope,
    workspace_cwd_scope,
)


def _lease(workspace_root="/work", writable=("/work",)):
    return ExecutionLease(
        ref=ExecutionLeaseRef(lease_id="L", generation="g", backend_id="fake"),
        state=LeaseState.READY,
        paths=WorkspacePaths(workspace_root=workspace_root, writable_roots=writable),
    )


def _routed(fake, lease=None):
    lease = lease or _lease()
    return (
        fs_io_scope(BackendFileIO(fake)),
        execution_lease_scope(lease),
        workspace_cwd_scope(Path("/tmp")),
    )


@pytest.mark.asyncio
async def test_routed_write_lands_in_backend_not_local_disk():
    fake = ex.FakeExecutionBackend()
    scopes = _routed(fake)
    with scopes[0], scopes[1], scopes[2]:
        await file_write_handler({"path": "/work/out.txt", "content": "routed-hello"})
    assert fake.files["/work/out.txt"] == "routed-hello"
    assert not Path("/work/out.txt").exists()  # never touched local disk


@pytest.mark.asyncio
async def test_routed_read_comes_from_backend():
    fake = ex.FakeExecutionBackend(files={"/work/a.txt": "from-backend"})
    scopes = _routed(fake)
    with scopes[0], scopes[1], scopes[2]:
        result = await read_file_handler({"path": "/work/a.txt"})
    assert "from-backend" in str(result)


@pytest.mark.asyncio
async def test_routed_relative_path_resolves_under_workspace_root():
    fake = ex.FakeExecutionBackend()
    scopes = _routed(fake)
    with scopes[0], scopes[1], scopes[2]:
        await file_write_handler({"path": "sub/rel.txt", "content": "x"})
    assert "/work/sub/rel.txt" in fake.files  # resolved under backend root


@pytest.mark.asyncio
async def test_routed_traversal_escape_rejected():
    fake = ex.FakeExecutionBackend(files={"/work/a.txt": "x"})
    scopes = _routed(fake)
    with scopes[0], scopes[1], scopes[2]:
        with pytest.raises(ex.WorkspacePathError):
            await read_file_handler({"path": "../etc/passwd"})


@pytest.mark.asyncio
async def test_routed_write_outside_writable_root_rejected():
    fake = ex.FakeExecutionBackend()
    lease = _lease(workspace_root="/work", writable=("/work/out",))
    scopes = _routed(fake, lease)
    with scopes[0], scopes[1], scopes[2]:
        with pytest.raises(ex.WorkspacePathError):
            await file_write_handler({"path": "/work/elsewhere.txt", "content": "x"})
        # a path under the writable root is fine
        await file_write_handler({"path": "/work/out/ok.txt", "content": "y"})
    assert fake.files["/work/out/ok.txt"] == "y"
