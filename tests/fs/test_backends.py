"""E7: pluggable filesystem backends — state, local, composite."""

from __future__ import annotations

import pytest

from agent_driver.fs import (
    CompositeBackend,
    FileBackend,
    FileBackendError,
    FileErrorCode,
    LocalFilesystemBackend,
    StateBackend,
)


@pytest.fixture(params=["state", "local"])
def backend(request, tmp_path) -> FileBackend:
    if request.param == "state":
        return StateBackend()
    return LocalFilesystemBackend(tmp_path / "root")


def test_is_filebackend(backend: FileBackend) -> None:
    assert isinstance(backend, FileBackend)


def test_write_read_roundtrip(backend: FileBackend) -> None:
    backend.write("notes/a.txt", "hello\nworld")
    assert backend.read("notes/a.txt") == "hello\nworld"


def test_read_missing_raises_not_found(backend: FileBackend) -> None:
    with pytest.raises(FileBackendError) as exc:
        backend.read("nope.txt")
    assert exc.value.code is FileErrorCode.NOT_FOUND


def test_edit_first_and_all(backend: FileBackend) -> None:
    backend.write("f.txt", "a a a")
    assert backend.edit("f.txt", "a", "b") == 1
    assert backend.read("f.txt") == "b a a"
    assert backend.edit("f.txt", "a", "c", replace_all=True) == 2
    assert backend.read("f.txt") == "b c c"


def test_edit_not_matched(backend: FileBackend) -> None:
    backend.write("f.txt", "abc")
    with pytest.raises(FileBackendError) as exc:
        backend.edit("f.txt", "zzz", "y")
    assert exc.value.code is FileErrorCode.NOT_MATCHED


def test_delete(backend: FileBackend) -> None:
    backend.write("f.txt", "x")
    backend.delete("f.txt")
    with pytest.raises(FileBackendError):
        backend.read("f.txt")


def test_ls_glob_grep(backend: FileBackend) -> None:
    backend.write("docs/a.md", "alpha\nTODO: x")
    backend.write("docs/b.txt", "beta")
    backend.write("src/c.md", "gamma TODO")
    assert backend.ls("docs/") == ["docs/a.md", "docs/b.txt"]
    assert backend.glob("*.md") == ["docs/a.md", "src/c.md"]
    hits = backend.grep("TODO", path_glob="*.md")
    assert {(p, n) for p, n, _ in hits} == {("docs/a.md", 2), ("src/c.md", 1)}


# --- LocalFilesystemBackend-specific safety ---------------------------------


def test_local_rejects_traversal(tmp_path) -> None:
    backend = LocalFilesystemBackend(tmp_path / "root")
    with pytest.raises(FileBackendError) as exc:
        backend.read("../../etc/passwd")
    assert exc.value.code is FileErrorCode.INVALID_PATH


def test_local_read_directory_is_directory(tmp_path) -> None:
    backend = LocalFilesystemBackend(tmp_path / "root")
    backend.write("dir/inner.txt", "x")
    with pytest.raises(FileBackendError) as exc:
        backend.read("dir")
    assert exc.value.code is FileErrorCode.IS_DIRECTORY


# --- CompositeBackend routing -----------------------------------------------


def test_composite_routes_by_prefix(tmp_path) -> None:
    persistent = LocalFilesystemBackend(tmp_path / "mem")
    ephemeral = StateBackend()
    scratch = StateBackend()
    fs = CompositeBackend({"/memories": persistent, "/tmp": scratch}, default=ephemeral)
    fs.write("/memories/fact.txt", "durable")
    fs.write("/tmp/scratch.txt", "ephemeral")
    fs.write("other.txt", "default")

    # Each landed in the right sub-backend (prefix stripped on delegation).
    assert persistent.read("fact.txt") == "durable"
    assert scratch.read("scratch.txt") == "ephemeral"
    assert ephemeral.read("other.txt") == "default"
    # And reads route back.
    assert fs.read("/memories/fact.txt") == "durable"


def test_composite_ls_aggregates_with_prefixes(tmp_path) -> None:
    fs = CompositeBackend(
        {"/memories": StateBackend(), "/tmp": StateBackend()},
        default=StateBackend(),
    )
    fs.write("/memories/a.txt", "1")
    fs.write("/tmp/b.txt", "2")
    fs.write("c.txt", "3")
    assert fs.ls() == ["/memories/a.txt", "/tmp/b.txt", "c.txt"]
    assert fs.ls("/memories") == ["/memories/a.txt"]


def test_composite_longest_prefix_wins() -> None:
    inner = StateBackend()
    outer = StateBackend()
    fs = CompositeBackend({"/a": outer, "/a/b": inner}, default=StateBackend())
    fs.write("/a/b/deep.txt", "inner")
    assert inner.read("deep.txt") == "inner"  # /a/b beats /a
    assert outer.ls("") == []


def test_composite_error_uses_composite_path(tmp_path) -> None:
    fs = CompositeBackend({"/memories": StateBackend()}, default=StateBackend())
    with pytest.raises(FileBackendError) as exc:
        fs.read("/memories/missing.txt")
    assert exc.value.code is FileErrorCode.NOT_FOUND
    assert exc.value.path == "/memories/missing.txt"
