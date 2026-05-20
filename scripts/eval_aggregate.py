#!/usr/bin/env python3
"""Aggregate eval summary metrics for one bundle."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import median
from typing import Any


def _percentile(values: list[int], p: float) -> int:
    if not values:
        return 0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = int(round((len(ordered) - 1) * p))
    rank = max(0, min(rank, len(ordered) - 1))
    return ordered[rank]


def _stats(values: list[int]) -> dict[str, int]:
    if not values:
        return {"min": 0, "median": 0, "p90": 0, "max": 0}
    ordered = sorted(values)
    return {
        "min": ordered[0],
        "median": int(median(ordered)),
        "p90": _percentile(ordered, 0.9),
        "max": ordered[-1],
    }


def _load_summary(bundle_dir: Path) -> list[dict[str, Any]]:
    summary_path = bundle_dir / "summary.json"
    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError("summary.json must contain JSON array")
    return [row for row in payload if isinstance(row, dict)]


def _aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tool_status: dict[str, dict[str, int]] = {}
    bug_tags: dict[str, int] = {}
    repeated_args: dict[str, int] = {}
    quality = {
        "answer_relevance": {"pass": 0, "partial": 0, "fail": 0},
        "tool_use_correctness": {"pass": 0, "partial": 0, "fail": 0},
        "efficiency": {"pass": 0, "partial": 0, "fail": 0},
    }
    elapsed_ms: list[int] = []
    llm_calls: list[int] = []
    tool_calls: list[int] = []
    steps_total: list[int] = []
    runtime_step_count: list[int] = []
    empty_tool_results_total = 0
    for row in rows:
        by_name = row.get("tools_by_name_status")
        if isinstance(by_name, dict):
            for tool_name, statuses in by_name.items():
                if not isinstance(statuses, dict):
                    continue
                bucket = tool_status.setdefault(str(tool_name), {})
                for status, count in statuses.items():
                    bucket[str(status)] = bucket.get(str(status), 0) + int(count)
        for tag in row.get("bug_tags", []):
            bug_tags[str(tag)] = bug_tags.get(str(tag), 0) + 1
        for arg_key in row.get("repeated_tool_arguments", []):
            repeated_args[str(arg_key)] = repeated_args.get(str(arg_key), 0) + 1
        for metric in quality:
            value = str(row.get(metric) or "")
            if value in quality[metric]:
                quality[metric][value] += 1
        elapsed_ms.append(int(row.get("elapsed_ms") or 0))
        llm_calls.append(int(row.get("llm_calls") or 0))
        tool_calls.append(int(row.get("tool_calls") or 0))
        steps_total.append(int(row.get("steps_total") or 0))
        runtime_step_raw = row.get("runtime_step_count")
        if isinstance(runtime_step_raw, int):
            runtime_step_count.append(runtime_step_raw)
        empty_tool_results_total += int(row.get("empty_tool_results") or 0)
    return {
        "scenario_count": len(rows),
        "tool_status_histogram": tool_status,
        "bug_tags": bug_tags,
        "repeated_tool_arguments": repeated_args,
        "empty_tool_results_total": empty_tool_results_total,
        "quality": quality,
        "stats": {
            "elapsed_ms": _stats(elapsed_ms),
            "llm_calls": _stats(llm_calls),
            "tool_calls": _stats(tool_calls),
            "steps_total": _stats(steps_total),
            "runtime_step_count": _stats(runtime_step_count),
        },
    }


def _render_markdown(payload: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"Scenarios: {payload['scenario_count']}")
    lines.append("")
    lines.append("## Tool status histogram")
    lines.append("")
    lines.append("| tool | status | count |")
    lines.append("|---|---|---:|")
    for tool in sorted(payload["tool_status_histogram"]):
        statuses = payload["tool_status_histogram"][tool]
        for status in sorted(statuses):
            lines.append(f"| {tool} | {status} | {statuses[status]} |")
    lines.append("")
    lines.append("## Numeric stats")
    lines.append("")
    lines.append("| metric | min | median | p90 | max |")
    lines.append("|---|---:|---:|---:|---:|")
    for metric in ("elapsed_ms", "llm_calls", "tool_calls", "steps_total", "runtime_step_count"):
        row = payload["stats"][metric]
        lines.append(
            f"| {metric} | {row['min']} | {row['median']} | {row['p90']} | {row['max']} |"
        )
    lines.append("")
    lines.append("## Quality")
    lines.append("")
    lines.append("| metric | pass | partial | fail |")
    lines.append("|---|---:|---:|---:|")
    for metric in ("answer_relevance", "tool_use_correctness", "efficiency"):
        row = payload["quality"][metric]
        lines.append(f"| {metric} | {row['pass']} | {row['partial']} | {row['fail']} |")
    lines.append("")
    lines.append("## Bug tags")
    lines.append("")
    lines.append("| tag | count |")
    lines.append("|---|---:|")
    for tag in sorted(payload["bug_tags"]):
        lines.append(f"| {tag} | {payload['bug_tags'][tag]} |")
    lines.append("")
    lines.append(f"empty_tool_results_total: {payload['empty_tool_results_total']}")
    lines.append("")
    lines.append("## Repeated tool argument keys")
    lines.append("")
    lines.append("| key | count |")
    lines.append("|---|---:|")
    repeated = payload["repeated_tool_arguments"]
    if repeated:
        for key in sorted(repeated, key=lambda item: (-repeated[item], item)):
            lines.append(f"| `{key}` | {repeated[key]} |")
    else:
        lines.append("| - | 0 |")
    return "\n".join(lines)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Aggregate one eval bundle summary.")
    parser.add_argument("bundle_dir", help="Path to .agent-driver/evals/<timestamp>")
    parser.add_argument("--json", action="store_true", help="Emit JSON payload instead of markdown")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    bundle_dir = Path(args.bundle_dir)
    summary_path = bundle_dir / "summary.json"
    if not summary_path.exists():
        print(f"error: missing summary file {summary_path}")
        return 2
    try:
        rows = _load_summary(bundle_dir)
    except (ValueError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2
    payload = _aggregate(rows)
    if args.json:
        print(json.dumps(payload, ensure_ascii=True, indent=2))
    else:
        print(_render_markdown(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
