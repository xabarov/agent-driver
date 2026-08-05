"""EPIC-03 WP-C (slice 2c) — artifact + notebook handlers route through backend."""

import json
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
from agent_driver.tools.builtin.filesystem.artifacts import (
    artifact_list_handler,
    artifact_read_handler,
)
from agent_driver.tools.builtin.filesystem.notebook import notebook_edit_handler
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


def _scopes(fake):
    return (
        fs_io_scope(BackendFileIO(fake)),
        execution_lease_scope(_lease()),
        workspace_cwd_scope(Path("/tmp")),
        workspace_backend_scope(fake),
    )


@pytest.mark.asyncio
async def test_routed_artifact_list_and_read_from_backend():
    fake = ex.FakeExecutionBackend(
        files={
            "/work/research/report.md": "# Title\nbody",
            "/work/tool-results/x.json": "{}",
            "/work/other.txt": "not an artifact",
        }
    )
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        listing = await artifact_list_handler({})
        paths = {a["path"] for a in listing["artifacts"]}
        assert paths == {
            "research/report.md",
            "tool-results/x.json",
        }  # other.txt excluded
        read = await artifact_read_handler({"path": "research/report.md"})
    assert "# Title" in read["content"]
    assert read["kind"] == "report"


@pytest.mark.asyncio
async def test_routed_artifact_read_rejects_non_artifact_and_traversal():
    fake = ex.FakeExecutionBackend(files={"/work/research/r.md": "x"})
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        with pytest.raises(ValueError):
            await artifact_read_handler({"path": "../etc/passwd"})
        with pytest.raises(ValueError):
            await artifact_read_handler({"path": "notes.txt"})  # not an artifact dir


@pytest.mark.asyncio
async def test_routed_notebook_edit_writes_to_backend_not_local_disk():
    nb = json.dumps(
        {
            "cells": [
                {
                    "cell_type": "code",
                    "source": ["x = 1"],
                    "metadata": {},
                    "execution_count": None,
                    "outputs": [],
                }
            ]
        }
    )
    fake = ex.FakeExecutionBackend(files={"/work/n.ipynb": nb})
    s = _scopes(fake)
    with s[0], s[1], s[2], s[3]:
        result = await notebook_edit_handler(
            {
                "path": "/work/n.ipynb",
                "cell_idx": 0,
                "is_new_cell": False,
                "old_text": "x = 1",
                "new_text": "x = 2",
            }
        )
    assert result["operation"] == "replace" and result["replacements"] == 1
    assert "x = 2" in fake.files["/work/n.ipynb"]  # written to the backend
    assert not Path("/work/n.ipynb").exists()  # never touched local disk
