"""Evaluation command handlers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from agent_driver.cli.evals import EvalSummary


async def eval_run_command(
    args: argparse.Namespace,
    *,
    scenarios_for_suite: Callable[[str], list[object]],
    run_live_evaluation: Callable[..., object],
    provider_config_from_args: Callable[[argparse.Namespace], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    store_config_from_args: Callable[[argparse.Namespace], object],
    provider_error: type[Exception],
    tool_error: type[Exception],
    live_eval_skipped: type[Exception],
) -> int:
    try:
        scenarios = scenarios_for_suite(args.suite)
    except ValueError as exc:
        print(f"eval error: {exc}")
        return 2
    try:
        bundle_dir, summaries = await run_live_evaluation(
            provider_config=provider_config_from_args(args),
            tool_config=tool_config_from_args(args),
            store_config=store_config_from_args(args),
            output_dir=Path(args.output_dir).resolve(),
            scenarios=scenarios,
            offline=args.offline,
        )
    except live_eval_skipped as exc:
        print(f"eval skip: {exc}")
        return 0
    except (provider_error, tool_error, RuntimeError) as exc:
        print(f"eval error: {exc}")
        return 2
    print(
        json.dumps(
            {
                "bundle_dir": str(bundle_dir),
                "scenarios": len(summaries),
                "failed": sum(1 for item in summaries if item.status != "completed"),
            },
            ensure_ascii=True,
        )
    )
    return 0


def eval_inspect_command(
    args: argparse.Namespace,
    *,
    render_eval_timeline: Callable[[dict[str, object]], str],
    render_eval_inspect: Callable[[EvalSummary], str],
) -> int:
    if bool(args.summary_json) == bool(args.artifact_json):
        print("eval inspect error: pass exactly one of --summary-json or --artifact-json")
        return 2
    if args.artifact_json:
        path = Path(args.artifact_json)
        if not path.exists():
            print(f"eval inspect error: missing artifact file {path}")
            return 2
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            print("eval inspect error: artifact file is not valid JSON")
            return 2
        if not isinstance(payload, dict):
            print("eval inspect error: artifact file must contain JSON object")
            return 2
        print(render_eval_timeline(payload))
        return 0
    path = Path(args.summary_json)
    if not path.exists():
        print(f"eval inspect error: missing summary file {path}")
        return 2
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        print("eval inspect error: summary file is not valid JSON")
        return 2
    if not isinstance(payload, list):
        print("eval inspect error: summary file must contain JSON list")
        return 2
    rows = payload
    if args.scenario_id:
        rows = [item for item in rows if isinstance(item, dict) and item.get("scenario_id") == args.scenario_id]
    if not rows:
        print("eval inspect> no rows")
        return 0
    defaults = {
        "tools_by_name_status": {},
        "repeated_tool_arguments": [],
    }
    for row in rows:
        if not isinstance(row, dict):
            continue
        summary = EvalSummary(**{**defaults, **row})
        print(render_eval_inspect(summary))
        print("")
    return 0


__all__ = ["eval_inspect_command", "eval_run_command"]
