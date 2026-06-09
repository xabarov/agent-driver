"""Standardized error codes for the pluggable filesystem backend (E7).

A single error type with a machine-readable ``code`` lets an LLM (or the
runtime) understand and recover from a file operation that failed — distinct
codes for "doesn't exist" vs "is a directory" vs "outside the sandbox" instead
of opaque ``OSError`` strings. Mirrors deepagents' backend error taxonomy.
"""

from __future__ import annotations

from agent_driver.contracts.enums import StrEnum


class FileErrorCode(StrEnum):
    """Recoverable file-operation error categories."""

    NOT_FOUND = "not_found"
    IS_DIRECTORY = "is_directory"
    ALREADY_EXISTS = "already_exists"
    INVALID_PATH = "invalid_path"  # malformed or escapes the backend's root
    NOT_MATCHED = "not_matched"  # edit() old-string not present


class FileBackendError(Exception):
    """A backend operation failed with a recoverable, categorized reason."""

    def __init__(self, code: FileErrorCode, path: str, message: str = "") -> None:
        self.code = code
        self.path = path
        super().__init__(message or f"{code.value}: {path}")


__all__ = ["FileBackendError", "FileErrorCode"]
