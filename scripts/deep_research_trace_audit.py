#!/usr/bin/env python3
"""Audit real Deep Research trace-summary artifacts.

This script does not mock chat behavior. It reads artifacts produced by live
Playwright runs, especially ``trace-summary.json`` files saved by
``examples/chat-demo/frontend/tests/e2e/chat_live_probe.py`` or
``scripts/deep_research_live_matrix.py``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


BLOCKING_FAILURE_FLAGS = (
    "missing_terminal_event",
    "run_failed_or_cancelled",
    "search_only_research_report",
    "plan_todos_incomplete_on_final",
    "deep_research_no_report_artifact",
    "deep_research_no_source_ledger_artifact",
    "deep_research_full_report_rewrite",
    "deep_research_stale_report_edit",
    "deep_research_repeated_report_read",
    "deep_research_final_missing_report_reference",
    "deep_research_missing_initial_todo",
    "deep_research_skill_denied",
    "deep_research_low_verified_coverage",
    "deep_research_preliminary_final",
    "deep_research_repeated_search_args",
    "deep_research_search_without_fetch_progress",
    "deep_research_tool_entropy_high",
    "deep_research_phase_violation",
    "deep_research_long_final_after_report",
)


@dataclass(frozen=True, slots=True)
class AuditRow:
    path: Path
    scenario: str
    run_id: str
    verdict: str
    terminal: str
    tools: str
    total_tokens: int
    output_tokens_after_report: int
    search_count: int
    fetch_count: int
    domain_count: int
    child_count: int
    report_updates: int
    full_writes: int
    stale_edits: int
    phase_violations: int
    risks: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.risks and self.verdict == "pass"


def main() -> int:
    args = parse_args()
    trace_paths = discover_trace_paths(args.paths)
    if not trace_paths:
        print("No trace-summary.json files found.", file=sys.stderr)
        return 2
    rows = [audit_trace(path) for path in trace_paths]
    if args.format == "json":
        print(json.dumps([row_to_json(row) for row in rows], indent=2))
    else:
        print(render_markdown(rows))
    if args.fail_on_risk and any(not row.ok for row in rows):
        return 1
    return 0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit live Deep Research trace-summary artifacts."
    )
    parser.add_argument(
        "paths",
        nargs="+",
        type=Path,
        help="trace-summary.json files or directories containing them.",
    )
    parser.add_argument(
        "--format",
        choices=("md", "json"),
        default="md",
        help="Output format.",
    )
    parser.add_argument(
        "--fail-on-risk",
        action="store_true",
        help="Exit 1 if any trace has blocking risk flags or non-pass verdict.",
    )
    return parser.parse_args()


def discover_trace_paths(paths: list[Path]) -> list[Path]:
    discovered: list[Path] = []
    for raw in paths:
        path = raw.expanduser()
        if path.is_file():
            discovered.append(path)
            continue
        if path.is_dir():
            discovered.extend(sorted(path.rglob("trace-summary.json")))
    return sorted(dict.fromkeys(item.resolve() for item in discovered))


def audit_trace(path: Path) -> AuditRow:
    payload = json.loads(path.read_text(encoding="utf-8"))
    llm = payload.get("llm") if isinstance(payload.get("llm"), dict) else {}
    usage = llm.get("usage") if isinstance(llm.get("usage"), dict) else {}
    research = (
        payload.get("research") if isinstance(payload.get("research"), dict) else {}
    )
    efficiency = (
        payload.get("research_efficiency")
        if isinstance(payload.get("research_efficiency"), dict)
        else {}
    )
    subagents = (
        payload.get("subagents") if isinstance(payload.get("subagents"), dict) else {}
    )
    failures = (
        payload.get("failures") if isinstance(payload.get("failures"), dict) else {}
    )
    risks = set()
    for flag in BLOCKING_FAILURE_FLAGS:
        if failures.get(flag) is True:
            risks.add(flag)
    if payload.get("verdict") != "pass":
        risks.add(f"verdict:{payload.get('verdict')}")
    if payload.get("terminal_event") is None:
        risks.add("missing_terminal_event")
    scenario = scenario_name_for(path)
    return AuditRow(
        path=path,
        scenario=scenario,
        run_id=str(payload.get("run_id") or ""),
        verdict=str(payload.get("verdict") or ""),
        terminal=str(payload.get("terminal_event") or ""),
        tools=str(payload.get("tool_chain") or ",".join(payload.get("tool_names") or [])),
        total_tokens=int(usage.get("total_tokens") or 0),
        output_tokens_after_report=int(
            efficiency.get("output_tokens_after_first_report_update") or 0
        ),
        search_count=int(research.get("search_count") or 0),
        fetch_count=int(research.get("fetch_count") or 0),
        domain_count=len(research.get("unique_domains") or []),
        child_count=int(subagents.get("runs_completed") or 0),
        report_updates=int(efficiency.get("report_update_count") or 0),
        full_writes=int(efficiency.get("report_full_write_count") or 0),
        stale_edits=int(
            efficiency.get("report_targeted_edit_without_fresh_read_count") or 0
        ),
        phase_violations=int(efficiency.get("phase_violation_count") or 0),
        risks=tuple(sorted(risks)),
    )


def scenario_name_for(path: Path) -> str:
    scenario_file = path.with_name("scenario.json")
    if scenario_file.is_file():
        try:
            payload = json.loads(scenario_file.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return path.parent.name
        if isinstance(payload.get("name"), str):
            return payload["name"]
    return path.parent.name


def render_markdown(rows: list[AuditRow]) -> str:
    lines = [
        "# Deep Research Trace Audit",
        "",
        "| scenario | verdict | tokens | search/fetch/domains | children | report updates | risks | run |",
        "| --- | --- | ---: | --- | ---: | --- | --- | --- |",
    ]
    for row in rows:
        risks = ", ".join(row.risks) if row.risks else "-"
        lines.append(
            "| "
            f"{row.scenario} | {row.verdict or '-'} | {row.total_tokens} | "
            f"{row.search_count}/{row.fetch_count}/{row.domain_count} | "
            f"{row.child_count} | "
            f"{row.report_updates} upd, {row.full_writes} full, "
            f"{row.stale_edits} stale, {row.phase_violations} phase | "
            f"{risks} | {row.run_id or '-'} |"
        )
    lines.extend(
        [
            "",
            "## Files",
            "",
        ]
    )
    for row in rows:
        lines.append(f"- `{row.scenario}`: `{row.path}`")
    return "\n".join(lines)


def row_to_json(row: AuditRow) -> dict[str, Any]:
    return {
        "path": str(row.path),
        "scenario": row.scenario,
        "run_id": row.run_id,
        "verdict": row.verdict,
        "terminal": row.terminal,
        "tools": row.tools,
        "total_tokens": row.total_tokens,
        "output_tokens_after_report": row.output_tokens_after_report,
        "search_count": row.search_count,
        "fetch_count": row.fetch_count,
        "domain_count": row.domain_count,
        "child_count": row.child_count,
        "report_updates": row.report_updates,
        "full_writes": row.full_writes,
        "stale_edits": row.stale_edits,
        "phase_violations": row.phase_violations,
        "risks": list(row.risks),
        "ok": row.ok,
    }


if __name__ == "__main__":
    raise SystemExit(main())
