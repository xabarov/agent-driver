"""EPIC-03 WP-C — workspace path safety + backend workspace operations."""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts.execution import ExecutionIdentity
from agent_driver.contracts.execution_lease import WorkspacePaths
from agent_driver.contracts.execution_workspace import (
    ExecutionDeleteRequest,
    ExecutionGlobRequest,
    ExecutionGrepRequest,
    ExecutionListRequest,
    ExecutionStatRequest,
)


def _id():
    return ExecutionIdentity(
        backend_id="fake", run_id="r", attempt_id="a", tool_call_id="t", request_id="q"
    )


# --------------------------------------------------------------------------- #
# path safety (security-critical; lexical, no disk)
# --------------------------------------------------------------------------- #
_PATHS = WorkspacePaths(workspace_root="/work", writable_roots=("/work/out",))


def test_relative_path_resolves_under_root():
    assert ex.validate_workspace_path("a/b.txt", _PATHS) == "/work/a/b.txt"


@pytest.mark.parametrize(
    "bad",
    ["../etc/passwd", "a/../../etc", "/etc/passwd", "/work/../secret", ""],
)
def test_traversal_and_escape_rejected(bad):
    with pytest.raises(ex.WorkspacePathError):
        ex.validate_workspace_path(bad, _PATHS)


def test_inner_dotdot_that_stays_within_root_is_allowed():
    assert ex.validate_workspace_path("a/b/../c.txt", _PATHS) == "/work/a/c.txt"


def test_writable_root_enforced():
    with pytest.raises(ex.WorkspacePathError):
        ex.validate_workspace_path("/work/readonly/f", _PATHS, require_writable=True)
    assert (
        ex.validate_workspace_path("/work/out/f", _PATHS, require_writable=True)
        == "/work/out/f"
    )


# --------------------------------------------------------------------------- #
# fake workspace operations + protocol membership
# --------------------------------------------------------------------------- #
def _backend():
    return ex.FakeExecutionBackend(
        files={
            "/work/a.txt": "hello\nworld",
            "/work/sub/b.py": "print('x')\nvalue = 1",
            "/work/sub/c.py": "value = 2",
        }
    )


def test_backend_is_workspace_capable():
    assert isinstance(_backend(), ex.WorkspaceCapableBackend)


@pytest.mark.asyncio
async def test_list_dir_immediate_and_recursive():
    be = _backend()
    top = await be.list_dir(
        ExecutionListRequest(identity=_id(), path="/work", max_entries=100)
    )
    names = {e.path for e in top.entries}
    assert "/work/a.txt" in names and "/work/sub" in names
    rec = await be.list_dir(
        ExecutionListRequest(
            identity=_id(), path="/work", recursive=True, max_entries=100
        )
    )
    assert "/work/sub/b.py" in {e.path for e in rec.entries}


@pytest.mark.asyncio
async def test_glob_matches_pattern():
    res = await _backend().glob(
        ExecutionGlobRequest(
            identity=_id(), base_path="/work", pattern="sub/*.py", max_entries=100
        )
    )
    assert set(res.paths) == {"/work/sub/b.py", "/work/sub/c.py"}


@pytest.mark.asyncio
async def test_grep_finds_matches_with_line_numbers():
    res = await _backend().grep(
        ExecutionGrepRequest(
            identity=_id(), base_path="/work", pattern=r"value", max_matches=100
        )
    )
    hits = {(m.path, m.line_number) for m in res.matches}
    assert ("/work/sub/b.py", 2) in hits
    assert ("/work/sub/c.py", 1) in hits


@pytest.mark.asyncio
async def test_grep_truncates_at_max():
    res = await _backend().grep(
        ExecutionGrepRequest(
            identity=_id(), base_path="/work", pattern=r"value", max_matches=1
        )
    )
    assert res.truncated is True and len(res.matches) == 1


@pytest.mark.asyncio
async def test_stat_file_and_dir_and_missing():
    be = _backend()
    f = await be.stat(ExecutionStatRequest(identity=_id(), path="/work/a.txt"))
    assert f.exists and not f.is_dir and f.size_bytes == len("hello\nworld".encode())
    d = await be.stat(ExecutionStatRequest(identity=_id(), path="/work/sub"))
    assert d.exists and d.is_dir
    m = await be.stat(ExecutionStatRequest(identity=_id(), path="/work/nope"))
    assert not m.exists


@pytest.mark.asyncio
async def test_delete_single_and_recursive_idempotent():
    be = _backend()
    r1 = await be.delete(ExecutionDeleteRequest(identity=_id(), path="/work/a.txt"))
    assert r1.deleted and "/work/a.txt" not in be.files
    # idempotent: deleting again is not an error
    r2 = await be.delete(ExecutionDeleteRequest(identity=_id(), path="/work/a.txt"))
    assert r2.deleted is False
    r3 = await be.delete(
        ExecutionDeleteRequest(identity=_id(), path="/work/sub", recursive=True)
    )
    assert r3.deleted and not any(p.startswith("/work/sub/") for p in be.files)
