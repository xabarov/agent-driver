"""Route the agent's file reads/writes through the ACP client (editor).

When the connected ACP client advertises the ``fs`` capability, file tool IO is
redirected to the editor via ``fs/read_text_file`` / ``fs/write_text_file`` so
the agent sees unsaved buffer contents and its edits land in the editor's
buffers. Per the spec the agent MUST only call a filesystem method the client
advertised — so each op falls back to local disk when its capability is absent.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import acp


class AcpClientFileIO:
    """An ``AsyncFileIO`` backed by the ACP client's filesystem methods."""

    def __init__(
        self,
        conn: "acp.Client",
        session_id: str,
        *,
        can_read: bool,
        can_write: bool,
    ) -> None:
        self._conn = conn
        self._session_id = session_id
        self._can_read = can_read
        self._can_write = can_write

    async def read_text(self, path: str) -> str:
        if not self._can_read:
            return Path(path).read_text(encoding="utf-8")
        response = await self._conn.read_text_file(
            path=path, session_id=self._session_id
        )
        return response.content or ""

    async def write_text(self, path: str, content: str) -> None:
        if not self._can_write:
            Path(path).write_text(content, encoding="utf-8")
            return
        await self._conn.write_text_file(
            content=content, path=path, session_id=self._session_id
        )


def client_fs_flags(client_capabilities: object | None) -> tuple[bool, bool]:
    """Extract ``(can_read, can_write)`` from an ACP client's capabilities."""
    fs = getattr(client_capabilities, "fs", None)
    if fs is None:
        return (False, False)
    return (
        bool(getattr(fs, "read_text_file", False)),
        bool(getattr(fs, "write_text_file", False)),
    )


__all__ = ["AcpClientFileIO", "client_fs_flags"]
