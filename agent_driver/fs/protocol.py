"""The pluggable filesystem backend protocol (E7).

A backend is a flat key→text store with a small, uniform operation set
(read/write/edit/ls/glob/grep/delete). Concrete backends (in-memory state,
local disk, composite path-prefix router, future S3/db/sandbox) implement this
so tools and the runtime can target one abstraction. Paths are POSIX-style
strings; backends normalize as needed. Errors are :class:`FileBackendError`
with a :class:`FileErrorCode`, never bare ``OSError``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class FileBackend(Protocol):
    """Uniform text-file operations over a pluggable storage backend."""

    def read(self, path: str) -> str:
        """Return the file's text, or raise ``NOT_FOUND`` / ``IS_DIRECTORY``."""

    def write(self, path: str, content: str) -> None:
        """Create or overwrite ``path`` with ``content``."""

    def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        """Replace ``old`` with ``new`` in ``path``; return the replacement count.

        Raises ``NOT_FOUND`` if the file is absent and ``NOT_MATCHED`` if ``old``
        does not occur. With ``replace_all=False`` exactly the first occurrence
        is replaced.
        """

    def delete(self, path: str) -> None:
        """Remove ``path``, or raise ``NOT_FOUND``."""

    def ls(self, prefix: str = "") -> list[str]:
        """Return sorted paths under ``prefix`` (empty = all)."""

    def glob(self, pattern: str) -> list[str]:
        """Return sorted paths matching the ``fnmatch`` ``pattern``."""

    def grep(self, pattern: str, *, path_glob: str = "*") -> list[tuple[str, int, str]]:
        """Regex-search files matching ``path_glob``; return (path, lineno, line)."""


__all__ = ["FileBackend"]
