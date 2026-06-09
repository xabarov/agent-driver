"""In-memory (ephemeral) filesystem backend (E7).

A process-local ``dict[path, text]`` store — the default scratch space and the
deterministic backend for tests. Lost when the process ends; pair it with a
durable backend under a ``CompositeBackend`` for anything that must survive.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch

from agent_driver.fs.errors import FileBackendError, FileErrorCode


class StateBackend:  # pylint: disable=missing-function-docstring
    """Ephemeral in-memory text-file backend.

    Methods implement :class:`~agent_driver.fs.protocol.FileBackend`; the op
    semantics are documented there.
    """

    def __init__(self) -> None:
        self._files: dict[str, str] = {}

    def read(self, path: str) -> str:
        if path not in self._files:
            raise FileBackendError(FileErrorCode.NOT_FOUND, path)
        return self._files[path]

    def write(self, path: str, content: str) -> None:
        if not path or path.endswith("/"):
            raise FileBackendError(FileErrorCode.INVALID_PATH, path)
        self._files[path] = content

    def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        content = self.read(path)
        if old not in content:
            raise FileBackendError(FileErrorCode.NOT_MATCHED, path)
        count = content.count(old) if replace_all else 1
        self._files[path] = content.replace(old, new, -1 if replace_all else 1)
        return count

    def delete(self, path: str) -> None:
        if path not in self._files:
            raise FileBackendError(FileErrorCode.NOT_FOUND, path)
        del self._files[path]

    def ls(self, prefix: str = "") -> list[str]:
        return sorted(p for p in self._files if p.startswith(prefix))

    def glob(self, pattern: str) -> list[str]:
        return sorted(p for p in self._files if fnmatch(p, pattern))

    def grep(self, pattern: str, *, path_glob: str = "*") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        hits: list[tuple[str, int, str]] = []
        for path in sorted(self._files):
            if not fnmatch(path, path_glob):
                continue
            for lineno, line in enumerate(self._files[path].splitlines(), start=1):
                if regex.search(line):
                    hits.append((path, lineno, line))
        return hits


__all__ = ["StateBackend"]
