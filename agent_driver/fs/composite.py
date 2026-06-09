"""Composite filesystem backend: route by path prefix (E7).

Dispatches each path to a sub-backend by its longest matching prefix — e.g.
``/memories`` → a durable backend, ``/tmp`` → an ephemeral one, everything else
→ a default. The matched prefix is stripped before delegating, and listing ops
re-prefix sub-backend paths back to their composite form. This lets one tool
surface span persistent + scratch + sandboxed storage transparently.
"""

from __future__ import annotations

import re
from fnmatch import fnmatch

from agent_driver.fs.errors import FileBackendError
from agent_driver.fs.protocol import FileBackend


class CompositeBackend:  # pylint: disable=missing-function-docstring
    """Route file operations to sub-backends by path prefix.

    Methods implement :class:`~agent_driver.fs.protocol.FileBackend`; the op
    semantics are documented there.
    """

    def __init__(self, routes: dict[str, FileBackend], default: FileBackend) -> None:
        # Normalize prefixes to a leading "/" and no trailing "/"; longest first
        # so the most specific route wins.
        self._routes: list[tuple[str, FileBackend]] = sorted(
            (("/" + prefix.strip("/"), backend) for prefix, backend in routes.items()),
            key=lambda item: len(item[0]),
            reverse=True,
        )
        self._default = default

    def _route(self, path: str) -> tuple[FileBackend, str, str]:
        """Return (backend, remapped_path, prefix) for ``path``."""
        norm = path if path.startswith("/") else "/" + path
        for prefix, backend in self._routes:
            if norm == prefix or norm.startswith(prefix + "/"):
                return backend, norm[len(prefix) :].lstrip("/"), prefix
        return self._default, path, ""

    def _compose(self, prefix: str, rel: str) -> str:
        return f"{prefix}/{rel}" if prefix else rel

    def read(self, path: str) -> str:
        backend, remapped, _ = self._route(path)
        return self._with_original_path(path, lambda: backend.read(remapped))

    def write(self, path: str, content: str) -> None:
        backend, remapped, _ = self._route(path)
        self._with_original_path(path, lambda: backend.write(remapped, content))

    def edit(self, path: str, old: str, new: str, *, replace_all: bool = False) -> int:
        backend, remapped, _ = self._route(path)
        return self._with_original_path(
            path, lambda: backend.edit(remapped, old, new, replace_all=replace_all)
        )

    def delete(self, path: str) -> None:
        backend, remapped, _ = self._route(path)
        self._with_original_path(path, lambda: backend.delete(remapped))

    def _all_paths(self) -> list[str]:
        paths: list[str] = []
        for prefix, backend in self._routes:
            paths.extend(self._compose(prefix, rel) for rel in backend.ls(""))
        paths.extend(self._default.ls(""))
        return sorted(set(paths))

    def ls(self, prefix: str = "") -> list[str]:
        norm = prefix if prefix.startswith("/") or not prefix else "/" + prefix
        return [p for p in self._all_paths() if p.startswith(norm)]

    def glob(self, pattern: str) -> list[str]:
        return [p for p in self._all_paths() if fnmatch(p, pattern)]

    def grep(self, pattern: str, *, path_glob: str = "*") -> list[tuple[str, int, str]]:
        regex = re.compile(pattern)
        hits: list[tuple[str, int, str]] = []
        for path in self._all_paths():
            if not fnmatch(path, path_glob):
                continue
            for lineno, line in enumerate(self.read(path).splitlines(), start=1):
                if regex.search(line):
                    hits.append((path, lineno, line))
        return hits

    @staticmethod
    def _with_original_path(path: str, op):  # type: ignore[no-untyped-def]
        """Run ``op`` but re-raise backend errors with the composite path."""
        try:
            return op()
        except FileBackendError as exc:
            raise FileBackendError(exc.code, path, str(exc)) from exc


__all__ = ["CompositeBackend"]
