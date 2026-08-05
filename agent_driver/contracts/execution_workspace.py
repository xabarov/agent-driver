"""Validated contracts for backend workspace operations (EPIC-03 WP-C).

Beyond command + text read/write (EPIC-01), a leased workspace also enumerates,
searches, stats, and deletes files. These are the typed requests/results a
``WorkspaceCapableBackend`` exchanges. Paths are BACKEND-RELATIVE: a local
``Path.resolve()`` cannot validate remote state, so the routing layer validates
against the lease's ``WorkspacePaths`` contract, not the local filesystem.
"""

from __future__ import annotations

from pydantic import Field

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.execution import ExecutionIdentity


class WorkspaceEntry(ContractModel):
    """One enumerated workspace path."""

    path: str = Field(min_length=1)
    is_dir: bool = False
    size_bytes: int | None = Field(default=None, ge=0)


class ExecutionListRequest(ContractModel):
    identity: ExecutionIdentity
    path: str = Field(min_length=1)
    recursive: bool = False
    max_entries: int = Field(gt=0)


class ExecutionListResult(ContractModel):
    identity: ExecutionIdentity
    entries: tuple[WorkspaceEntry, ...] = ()
    truncated: bool = False


class ExecutionGlobRequest(ContractModel):
    identity: ExecutionIdentity
    base_path: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    max_entries: int = Field(gt=0)


class ExecutionGlobResult(ContractModel):
    identity: ExecutionIdentity
    paths: tuple[str, ...] = ()
    truncated: bool = False


class GrepMatch(ContractModel):
    path: str = Field(min_length=1)
    line_number: int = Field(ge=1)
    line: str


class ExecutionGrepRequest(ContractModel):
    identity: ExecutionIdentity
    base_path: str = Field(min_length=1)
    pattern: str = Field(min_length=1)
    path_glob: str | None = None
    max_matches: int = Field(gt=0)


class ExecutionGrepResult(ContractModel):
    identity: ExecutionIdentity
    matches: tuple[GrepMatch, ...] = ()
    truncated: bool = False


class ExecutionStatRequest(ContractModel):
    identity: ExecutionIdentity
    path: str = Field(min_length=1)


class ExecutionStatResult(ContractModel):
    identity: ExecutionIdentity
    path: str
    exists: bool
    is_dir: bool = False
    size_bytes: int | None = Field(default=None, ge=0)


class ExecutionDeleteRequest(ContractModel):
    identity: ExecutionIdentity
    path: str = Field(min_length=1)
    recursive: bool = False


class ExecutionDeleteResult(ContractModel):
    identity: ExecutionIdentity
    path: str
    deleted: bool


__all__ = [
    "WorkspaceEntry",
    "ExecutionListRequest",
    "ExecutionListResult",
    "ExecutionGlobRequest",
    "ExecutionGlobResult",
    "GrepMatch",
    "ExecutionGrepRequest",
    "ExecutionGrepResult",
    "ExecutionStatRequest",
    "ExecutionStatResult",
    "ExecutionDeleteRequest",
    "ExecutionDeleteResult",
]
