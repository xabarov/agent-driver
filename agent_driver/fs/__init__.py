"""Pluggable filesystem backends (E7).

A uniform :class:`FileBackend` protocol (read/write/edit/ls/glob/grep/delete)
with standardized :class:`FileBackendError` codes, plus three backends — an
ephemeral in-memory ``StateBackend``, a jailed-to-root ``LocalFilesystemBackend``,
and a ``CompositeBackend`` that routes by path prefix. Tools and the runtime can
target one abstraction across scratch / durable / sandboxed storage.
"""

from agent_driver.fs.composite import CompositeBackend
from agent_driver.fs.errors import FileBackendError, FileErrorCode
from agent_driver.fs.local import LocalFilesystemBackend
from agent_driver.fs.protocol import FileBackend
from agent_driver.fs.state import StateBackend

__all__ = [
    "CompositeBackend",
    "FileBackend",
    "FileBackendError",
    "FileErrorCode",
    "LocalFilesystemBackend",
    "StateBackend",
]
