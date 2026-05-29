#!/usr/bin/env python3
"""Audit dangerous tool paths are confined to eval sandbox."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

_DANGEROUS = {"file_write", "file_edit", "bash"}


def _load_artifacts(bundle_dir: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    for path in sorted(bundle_dir.glob("*.json")):
        if path.name in {"manifest.json", "summary.json", "triage.json"}:
            continue
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            payload["_artifact_path"] = str(path)
            artifacts.append(payload)
    return artifacts


def _resolve_candidate_path(
    *, tool_name: str, args: dict[str, Any], scenario_sandbox: Path
) -> tuple[Path | None, str]:
    if tool_name in {"file_write", "file_edit"}:
        raw_path = args.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return None, "missing_path"
        candidate = Path(raw_path).expanduser()
        if not candidate.is_absolute():
            candidate = (scenario_sandbox / candidate).resolve()
        return candidate, "path"
    if tool_name == "bash":
        raw_cwd = args.get("cwd")
        if isinstance(raw_cwd, str) and raw_cwd.strip():
            candidate = Path(raw_cwd).expanduser()
            if not candidate.is_absolute():
                candidate = (scenario_sandbox / candidate).resolve()
            return candidate, "cwd"
        return None, "cwd_missing"
    return None, "unsupported"


def _extract_args(tool_row: dict[str, Any]) -> dict[str, Any]:
    metadata = tool_row.get("metadata")
    if isinstance(metadata, dict):
        args = metadata.get("args")
        if isinstance(args, dict):
            return args
    args_summary = tool_row.get("args_summary")
    if isinstance(args_summary, dict):
        return args_summary
    return {}


def _audit_artifact(bundle_dir: Path, artifact: dict[str, Any]) -> list[tuple[str, ...]]:
    rows: list[tuple[str, ...]] = []
    scenario = artifact.get("scenario")
    if not isinstance(scenario, dict):
        return rows
    if not bool(scenario.get("sandbox_required")):
        return rows
    scenario_id = str(scenario.get("scenario_id") or "unknown")
    sandbox_dir_raw = scenario.get("sandbox_dir")
    if not isinstance(sandbox_dir_raw, str) or not sandbox_dir_raw:
        rows.append((scenario_id, "-", "-", "-", "missing_sandbox_dir"))
        return rows
    scenario_sandbox = Path(sandbox_dir_raw).resolve()
    if not scenario_sandbox.exists():
        rows.append((scenario_id, "-", "-", str(scenario_sandbox), "missing_sandbox_path"))
        return rows
    tool_trace = artifact.get("tool_trace")
    if not isinstance(tool_trace, list):
        return rows
    for row in tool_trace:
        if not isinstance(row, dict):
            continue
        tool_name = str(row.get("tool_name") or "")
        if tool_name not in _DANGEROUS:
            continue
        status = str(row.get("status") or "")
        args = _extract_args(row)
        candidate, source = _resolve_candidate_path(
            tool_name=tool_name, args=args, scenario_sandbox=scenario_sandbox
        )
        if candidate is None:
            rows.append((scenario_id, tool_name, status, source, "missing_path_data"))
            continue
        verdict = "ok"
        try:
            candidate.relative_to(scenario_sandbox)
        except ValueError:
            verdict = "escape"
        rows.append((scenario_id, tool_name, status, str(candidate), verdict))
    if not rows:
        rows.append((scenario_id, "-", "-", str(bundle_dir), "no_dangerous_calls"))
    return rows


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Audit dangerous tool paths in eval bundle.")
    parser.add_argument("bundle_dir", help="Path to .agent-driver/evals/<timestamp>")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    bundle_dir = Path(args.bundle_dir)
    if not bundle_dir.exists():
        print(f"error: bundle directory does not exist: {bundle_dir}")
        return 2
    try:
        artifacts = _load_artifacts(bundle_dir)
    except (OSError, json.JSONDecodeError) as exc:
        print(f"error: {exc}")
        return 2
    all_rows: list[tuple[str, ...]] = []
    for artifact in artifacts:
        all_rows.extend(_audit_artifact(bundle_dir, artifact))
    print("scenario_id,tool_name,status,path_or_source,verdict")
    has_escape = False
    for row in all_rows:
        if row[-1] == "escape":
            has_escape = True
        print(",".join(row))
    return 1 if has_escape else 0


if __name__ == "__main__":
    raise SystemExit(main())
