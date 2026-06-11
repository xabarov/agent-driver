"""Local-disk filesystem backend, jailed under a root directory (E7).

Maps backend paths to files beneath ``root``; every path is resolved and
verified to stay inside ``root`` (a traversal like ``../etc/passwd`` raises
``INVALID_PATH``). Intended as the durable tier of a ``CompositeBackend``.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch
from pathlib import Path

from agent_driver.fs.errors import FileBackendError, FileErrorCode


class LocalFilesystemBackend:  # pylint: disable=missing-function-docstring
    """Disk-backed text-file backend contained under ``root``.

    Methods implement :class:`~agent_driver.fs.protocol.FileBackend`; the op
    semantics are documented there.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).expanduser().resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve(self, path: str) -> Path:
        if not path:
            raise FileBackendError(FileErrorCode.INVALID_PATH, path)
        candidate = (self._root / path.lstrip("/")).resolve()
        try:
            candidate.relative_to(self._root)
        except ValueError as exc:
            raise FileBackendError(
                FileErrorCode.INVALID_PATH, path, "path escapes the backend root"
            ) from exc
        return candidate

    def _rel(self, resolved: Path) -> str:
        return resolved.relative_to(self._root).as_posix()

    def read(self, path: str) -> str:
        target = self._resolve(path)
        if target.is_dir():
            raise FileBackendError(FileErrorCode.IS_DIRECTORY, path)
        if not target.is_file():
            raise FileBackendError(FileErrorCode.NOT_FOUND, path)
        return target.read_text(encoding="utf-8")

    def write(self, path: str, content: str) -> None:
        target = self._resolve(path)
        if target.is_dir():
            raise FileBackendError(FileErrorCode.IS_DIRECTORY, path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        content = self.read(path)
        if old not in content:
            raise FileBackendError(FileErrorCode.NOT_MATCHED, path)
        count = content.count(old) if replace_all else 1
        self.write(path, content.replace(old, new, -1 if replace_all else 1))
        return count

    def delete(self, path: str) -> None:
        target = self._resolve(path)
        if not target.is_file():
            raise FileBackendError(FileErrorCode.NOT_FOUND, path)
        target.unlink()

    def _all_files(self) -> list[str]:
        return sorted(self._rel(p) for p in self._root.rglob("*") if p.is_file())

    def ls(self, prefix: str = "") -> list[str]:
        norm = prefix.lstrip("/")
        return [p for p in self._all_files() if p.startswith(norm)]

    def glob(self, pattern: str) -> list[str]:
        norm = pattern.lstrip("/")
        return [p for p in self._all_files() if fnmatch(p, norm)]

    def grep(self, pattern: str, *, path_glob: str = "*") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        glob_norm = path_glob.lstrip("/")
        hits: list[tuple[str, int, str]] = []
        for rel in self._all_files():
            if not fnmatch(rel, glob_norm):
                continue
            text = (self._root / rel).read_text(encoding="utf-8")
            for lineno, line in enumerate(text.splitlines(), start=1):
                if regex.search(line):
                    hits.append((rel, lineno, line))
        return hits


__all__ = ["LocalFilesystemBackend"]
