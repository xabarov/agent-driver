"""Tests keeping built-in pack docs aligned with ToolSet definitions."""

from __future__ import annotations

from pathlib import Path

from agent_driver.tools.toolset import builtin_pack_names


def test_builtin_tools_doc_pack_names_match_toolset() -> None:
    """docs/builtin-tools.md pack list should match ToolSet packs."""
    doc_path = Path(__file__).resolve().parents[2] / "docs" / "builtin-tools.md"
    text = doc_path.read_text(encoding="utf-8")
    marker = "Доступные packs:"
    if marker not in text:
        raise AssertionError("builtin-tools.md is missing pack marker")
    section = text.split(marker, maxsplit=1)[1].split("## ", maxsplit=1)[0]
    documented: list[str] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("- `"):
            continue
        pack = stripped.split("`", maxsplit=2)[1]
        documented.append(pack)
    assert tuple(sorted(documented)) == builtin_pack_names()
