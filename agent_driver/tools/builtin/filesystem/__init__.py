"""Filesystem and codebase analysis built-in tools."""

from __future__ import annotations

from agent_driver.tools.builtin.filesystem.notebook import (
    notebook_edit_handler,
    notebook_edit_manifest,
)
from agent_driver.tools.builtin.filesystem.read import read_file_handler, read_file_manifest
from agent_driver.tools.builtin.filesystem.search import (
    glob_search_handler,
    glob_search_manifest,
    grep_search_handler,
    grep_search_manifest,
)
from agent_driver.tools.builtin.filesystem.write import (
    file_edit_handler,
    file_edit_manifest,
    file_write_handler,
    file_write_manifest,
)
from agent_driver.tools.registry import ToolRegistry


def register_filesystem_tools(registry: ToolRegistry) -> None:
    """Register built-in read/search tools for local codebase analysis."""
    registry.register(read_file_manifest(), read_file_handler)
    registry.register(glob_search_manifest(), glob_search_handler)
    registry.register(grep_search_manifest(), grep_search_handler)
    registry.register(file_write_manifest(), file_write_handler)
    registry.register(file_edit_manifest(), file_edit_handler)
    registry.register(notebook_edit_manifest(), notebook_edit_handler)


__all__ = ["register_filesystem_tools"]
