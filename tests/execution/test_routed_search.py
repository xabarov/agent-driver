"""EPIC-03 WP-C (slice 2b) — glob/grep handlers route through the backend.

When a workspace-capable backend is active, glob_search/grep_search enumerate and
search the BACKEND filesystem (no local disk walk). Local behavior is unchanged
when no workspace backend is installed.
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
from agent_driver.tools.builtin.filesystem.search import (
    glob_search_handler,
    grep_search_handler,
)
from agent_driver.tools.context import (
    execution_lease_scope,
    fs_io_scope,
    workspace_backend_scope,
    workspace_cwd_scope,
)


def _lease():
    return ExecutionLease(
        ref=ExecutionLeaseRef(lease_id="L", generation="g", backend_id="fake"),
        state=LeaseState.READY,
        paths=WorkspacePaths(workspace_root="/work", writable_roots=("/work",)),
    )


def _backend():
    return ex.FakeExecutionBackend(
        files={
            "/work/a.py": "x = 1\nvalue = 2",
            "/work/sub/b.py": "value = 3",
            "/work/c.txt": "nothing here",
        }
    )


def _scopes(fake):
    return (
        fs_io_scope(BackendFileIO(fake)),
        execution_lease_scope(_lease()),
        workspace_cwd_scope(Path("/tmp")),
        workspace_backend_scope(fake),
    )


@pytest.mark.asyncio
async def test_routed_glob_enumerates_backend_not_local_disk():
    fake = _backend()
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        res = await glob_search_handler({"pattern": "*.py"})
    assert set(res["results"]) == {"/work/a.py", "/work/sub/b.py"}
    assert res["results"]  # came from the backend fs, not the local cwd


@pytest.mark.asyncio
async def test_routed_glob_truncates_via_backend():
    fake = _backend()
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        res = await glob_search_handler({"pattern": "*.py", "max_results": 1})
    assert res["truncated"] is True
    assert len(res["results"]) == 1


@pytest.mark.asyncio
async def test_routed_grep_searches_backend_with_line_numbers():
    fake = _backend()
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        res = await grep_search_handler({"pattern": "value"})
    hits = {(m["path"], m["line"]) for m in res["matches"]}
    assert ("/work/a.py", 2) in hits
    assert ("/work/sub/b.py", 1) in hits


@pytest.mark.asyncio
async def test_routed_grep_truncates_via_backend():
    fake = _backend()
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        res = await grep_search_handler({"pattern": "value", "max_matches": 1})
    assert res["truncated"] is True
    assert len(res["matches"]) == 1
