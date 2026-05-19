"""Lightweight read-only LSP-style tool."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from agent_driver.contracts import ApprovalMode, SideEffectClass, ToolManifest, ToolRisk
from agent_driver.tools.builtin.filesystem._paths import (
    resolve_base_dir,
    resolve_file_path,
)
from agent_driver.tools.registry import ToolRegistry

_LSP_TOOL = "lsp_tool"
_SYMBOL_RE = re.compile(r"^\s*(?:def|class)\s+([A-Za-z_][A-Za-z0-9_]*)")


def register_lsp_tools(registry: ToolRegistry) -> None:
    """Register lightweight LSP-style read-only tool."""
    registry.register(_lsp_manifest(), _lsp_handler)


def _lsp_manifest() -> ToolManifest:
    return ToolManifest(
        name=_LSP_TOOL,
        description="Lightweight read-only code intelligence (symbols/definitions/references).",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        timeout_seconds=15.0,
        output_char_budget=9000,
        idempotent=True,
        args_schema={
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": ["symbols", "definitions", "references"],
                },
                "path": {
                    "type": "string",
                    "description": "Absolute file path for symbols",
                },
                "symbol": {"type": "string", "description": "Symbol name for lookup"},
                "base_dir": {
                    "type": "string",
                    "description": "Absolute base dir for references/definitions scan",
                },
                "max_results": {"type": "integer", "minimum": 1, "maximum": 500},
            },
            "required": ["operation"],
            "additionalProperties": False,
        },
        output_type="json",
    )


async def _lsp_handler(args: dict[str, Any]) -> dict[str, Any]:
    operation = str(args.get("operation") or "").strip().lower()
    max_results = int(args.get("max_results") or 50)
    if operation == "symbols":
        target = resolve_file_path(args.get("path"))
        rows = _symbols_for_file(target=target, max_results=max_results)
        return {"summary": f"{len(rows)} symbols in {target.name}", "symbols": rows}
    if operation in {"definitions", "references"}:
        symbol = str(args.get("symbol") or "").strip()
        if not symbol:
            raise ValueError("symbol is required for definitions/references")
        base = resolve_base_dir(args.get("base_dir"))
        rows = _search_symbol_rows(
            base=base,
            symbol=symbol,
            max_results=max_results,
            definitions_only=operation == "definitions",
        )
        return {
            "summary": f"{len(rows)} {operation} for '{symbol}'",
            "symbol": symbol,
            "results": rows,
        }
    raise ValueError("operation must be one of: symbols, definitions, references")


def _symbols_for_file(*, target: Path, max_results: int) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    for idx, line in enumerate(lines, start=1):
        match = _SYMBOL_RE.match(line)
        if match is None:
            continue
        rows.append({"name": match.group(1), "line": idx, "kind": _kind_for_line(line)})
        if len(rows) >= max_results:
            break
    return rows


def _search_symbol_rows(
    *, base: Path, symbol: str, max_results: int, definitions_only: bool
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    def_pattern = re.compile(rf"^\s*(?:def|class)\s+{re.escape(symbol)}\b")
    ref_pattern = re.compile(rf"\b{re.escape(symbol)}\b")
    for path in sorted(base.rglob("*")):
        if not path.is_file():
            continue
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (UnicodeDecodeError, OSError):
            continue
        rel = path.relative_to(base).as_posix()
        for idx, line in enumerate(lines, start=1):
            if definitions_only:
                if def_pattern.search(line) is None:
                    continue
            else:
                if ref_pattern.search(line) is None:
                    continue
            rows.append({"path": rel, "line": idx, "text": line[:300]})
            if len(rows) >= max_results:
                return rows
    return rows


def _kind_for_line(line: str) -> str:
    stripped = line.lstrip()
    if stripped.startswith("def "):
        return "function"
    return "class"


__all__ = ["register_lsp_tools"]
