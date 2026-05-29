"""Tests for lightweight LSP tool."""

from __future__ import annotations

import pytest

from agent_driver.tools.builtin.lsp import register_lsp_tools
from agent_driver.tools.registry import ToolRegistry


@pytest.mark.asyncio
async def test_lsp_tool_symbols_and_references(tmp_path) -> None:
    """lsp_tool should return symbols and reference matches deterministically."""
    src = tmp_path / "pkg"
    src.mkdir()
    file_a = src / "a.py"
    file_b = src / "b.py"
    file_a.write_text(
        "class Alpha:\n    pass\n\ndef beta():\n    return Alpha\n", encoding="utf-8"
    )
    file_b.write_text("from .a import Alpha\nx = Alpha\n", encoding="utf-8")
    registry = ToolRegistry()
    register_lsp_tools(registry)
    tool = registry.get("lsp_tool")
    assert tool is not None
    symbols = await tool.handler({"operation": "symbols", "path": str(file_a)})
    assert symbols["symbols"]
    assert any(row["name"] == "Alpha" for row in symbols["symbols"])
    defs = await tool.handler(
        {"operation": "definitions", "base_dir": str(tmp_path), "symbol": "Alpha"}
    )
    assert defs["results"]
    refs = await tool.handler(
        {"operation": "references", "base_dir": str(tmp_path), "symbol": "Alpha"}
    )
    assert len(refs["results"]) >= len(defs["results"])


@pytest.mark.asyncio
async def test_lsp_tool_reports_truncated_metadata(tmp_path) -> None:
    """lsp_tool should expose truncation metadata for capped result sets."""
    src = tmp_path / "pkg"
    src.mkdir()
    file_a = src / "a.py"
    file_a.write_text(
        "class Alpha:\n    pass\n\ndef beta():\n    pass\n\ndef gamma():\n    pass\n",
        encoding="utf-8",
    )
    registry = ToolRegistry()
    register_lsp_tools(registry)
    tool = registry.get("lsp_tool")
    assert tool is not None
    symbols = await tool.handler({"operation": "symbols", "path": str(file_a), "max_results": 1})
    assert symbols["returned_count"] == 1
    assert symbols["truncated"] is True
    with pytest.raises(ValueError, match=">= 1"):
        await tool.handler({"operation": "symbols", "path": str(file_a), "max_results": 0})
