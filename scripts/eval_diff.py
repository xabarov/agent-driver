#!/usr/bin/env python3
"""Diff two eval bundles and report regressions."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def _load_summary(bundle: Path) -> dict[str, dict[str, Any]]:
    path = bundle / "summary.json"
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"{path} must contain JSON array")
    rows: dict[str, dict[str, Any]] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        scenario_id = item.get("scenario_id")
        if isinstance(scenario_id, str):
            rows[scenario_id] = item
    return rows


def _delta_int(before: dict[str, Any], after: dict[str, Any], key: str) -> int:
    return int(after.get(key) or 0) - int(before.get(key) or 0)


def _as_set(row: dict[str, Any], key: str) -> set[str]:
    value = row.get(key)
    if not isinstance(value, list):
        return set()
    return {str(item) for item in value}


def _is_regression(before: dict[str, Any], after: dict[str, Any], new_tags: set[str]) -> bool:
    if str(before.get("status")) != "failed" and str(after.get("status")) == "failed":
        return True
    for metric in ("answer_relevance", "tool_use_correctness", "efficiency"):
        if str(before.get(metric)) != "fail" and str(after.get(metric)) == "fail":
            return True
    if _as_set(after, "forbidden_tools_used"):
        return True
    if _as_set(after, "required_tools_missing"):
        return True
    if any(tag != "none" for tag in new_tags):
        return True
    return False


def _render_diff(
    before_rows: dict[str, dict[str, Any]], after_rows: dict[str, dict[str, Any]]
) -> tuple[str, bool]:
    regressions = False
    lines: list[str] = []
    lines.append("| scenario_id | status | quality | delta | tags | regression |")
    lines.append("|---|---|---|---|---|---|")
    all_ids = sorted(set(before_rows) | set(after_rows))
    for scenario_id in all_ids:
        before = before_rows.get(scenario_id)
        after = after_rows.get(scenario_id)
        if before is None:
            lines.append(f"| {scenario_id} | new | new | - | - | no |")
            continue
        if after is None:
            lines.append(f"| {scenario_id} | removed | removed | - | - | no |")
            continue
        status = f"{before.get('status')} -> {after.get('status')}"
        quality = (
            f"ans:{before.get('answer_relevance')}->{after.get('answer_relevance')}; "
            f"tools:{before.get('tool_use_correctness')}->{after.get('tool_use_correctness')}; "
            f"eff:{before.get('efficiency')}->{after.get('efficiency')}"
        )
        delta = (
            f"elapsed={_delta_int(before, after, 'elapsed_ms'):+d}, "
            f"llm={_delta_int(before, after, 'llm_calls'):+d}, "
            f"tools={_delta_int(before, after, 'tool_calls'):+d}, "
            f"answer_len={_delta_int(before, after, 'answer_length'):+d}, "
            f"runtime_steps={_delta_int(before, after, 'runtime_step_count'):+d}"
        )
        before_tags = _as_set(before, "bug_tags")
        after_tags = _as_set(after, "bug_tags")
        added_tags = sorted(after_tags - before_tags)
        removed_tags = sorted(before_tags - after_tags)
        tags = f"+{added_tags} -{removed_tags}"
        regression = _is_regression(before, after, set(added_tags))
        regressions = regressions or regression
        lines.append(
            f"| {scenario_id} | {status} | {quality} | {delta} | {tags} | {'yes' if regression else 'no'} |"
        )
        for key in ("expected_tools_missing", "required_tools_missing", "forbidden_tools_used"):
            before_set = _as_set(before, key)
            after_set = _as_set(after, key)
            add = sorted(after_set - before_set)
            rem = sorted(before_set - after_set)
            if add or rem:
                lines.append(f"| {scenario_id} | {key} | +{add} -{rem} | - | - | - |")
    return "\n".join(lines), regressions


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diff two eval bundles.")
    parser.add_argument("bundle_a", help="Baseline bundle directory")
    parser.add_argument("bundle_b", help="Candidate bundle directory")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    bundle_a = Path(args.bundle_a)
    bundle_b = Path(args.bundle_b)
    try:
        before_rows = _load_summary(bundle_a)
        after_rows = _load_summary(bundle_b)
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"error: {exc}")
        return 2
    report, has_regression = _render_diff(before_rows, after_rows)
    print(report)
    return 1 if has_regression else 0


if __name__ == "__main__":
    raise SystemExit(main())
