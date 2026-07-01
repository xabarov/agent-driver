"""Evaluation command handlers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from agent_driver.cli.evals import EvalSummary
from agent_driver.cli.providers import provider_config_for_eval


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
        offline_mode = bool(args.offline) or bool(
            getattr(args, "allow_live_without_env", False)
        )
        bundle_dir, summaries = await run_live_evaluation(
            provider_config=provider_config_for_eval(provider_config_from_args(args)),
            tool_config=tool_config_from_args(args),
            store_config=store_config_from_args(args),
            output_dir=Path(args.output_dir).resolve(),
            scenarios=scenarios,
            offline=offline_mode,
            continue_on_error=bool(getattr(args, "continue_on_error", False)),
        )
    except live_eval_skipped as exc:
        print(f"eval skip: {exc}")
        return 0
    except (provider_error, tool_error, RuntimeError) as exc:
        print(f"eval error: {exc}")
        return 2
    failures_path = bundle_dir / "failures.json"
    failure_rows: list[dict[str, str]] = []
    if failures_path.exists():
        try:
            loaded = json.loads(failures_path.read_text(encoding="utf-8"))
            if isinstance(loaded, list):
                failure_rows = [row for row in loaded if isinstance(row, dict)]
        except json.JSONDecodeError:
            failure_rows = []
    payload = {
        "bundle_dir": str(bundle_dir),
        "scenarios": len(summaries),
        "failed": sum(1 for item in summaries if item.status != "completed"),
        "scenario_errors": len(failure_rows),
    }
    print(json.dumps(payload, ensure_ascii=True))
    if failure_rows:
        return 2
    return 0


async def eval_compare_command(  # pylint: disable=import-outside-toplevel
    args: argparse.Namespace,
    *,
    build_cli_toolset: Callable[[object], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    tool_error: type[Exception],
) -> int:
    """Run the general suite baseline-vs-treatment on an open-weight tier.

    Flips exactly one harness axis (``--treatment``) off vs on and reports the
    median delta. ``--offline`` uses the fake provider for a deterministic dry
    run (no network); otherwise the open-weight OpenRouter preset is used.

    Imports are function-local to keep the heavier eval/batch subtree off the
    CLI's startup path (it only loads when ``eval compare`` actually runs).
    """
    from agent_driver.batch import BatchRunner
    from agent_driver.evals import (
        general_task_suite,
        openweight_provider_spec,
        render_comparison,
        run_comparison,
    )
    from agent_driver.llm.providers_impl.fake import FakeProvider
    from agent_driver.runtime import RunnerConfig
    from agent_driver.sdk import create_agent

    offline = bool(getattr(args, "offline", False))
    tier = str(getattr(args, "tier", "mid"))
    axis = str(getattr(args, "treatment", "prompt_cache"))

    # Each axis maps to (config builder over a treatment flag, baseline label,
    # treatment label). Only axes that flip cleanly off/on over the general
    # suite are offered; per-model auxiliary routing and subagent routing need a
    # richer suite/second provider, so they stay SDK-only.
    axes = {
        "prompt_cache": (
            lambda t: RunnerConfig(enable_prompt_cache=t),
            "prompt_cache_off",
            "prompt_cache_on",
        ),
        "tool_arg_truncation": (
            lambda t: RunnerConfig(enable_tool_arg_truncation=t),
            "arg_trunc_off",
            "arg_trunc_on",
        ),
        "tool_concurrency": (
            lambda t: RunnerConfig(tool_concurrency_limit=None if t else 1),
            "serial",
            "parallel",
        ),
        "budget_grace": (
            lambda t: RunnerConfig(budget_grace_enabled=t),
            "grace_off",
            "grace_on",
        ),
    }
    if axis not in axes:
        print(f"eval compare error: unknown --treatment axis {axis!r}")
        return 2
    config_for, baseline_label, treatment_label = axes[axis]

    def _provider():
        if offline:
            return FakeProvider(response_text="done")
        from agent_driver.llm import resolve_provider

        return resolve_provider(openweight_provider_spec(tier))

    # No-op-axis guard: prompt_cache only does anything on the Anthropic
    # provider, but this command resolves the open-weight OpenRouter preset, so
    # both sides send identical requests — any "delta" is environment/noise, not
    # the flag. Warn so the operator picks an axis that's actually active here.
    if axis == "prompt_cache" and not offline:
        print(
            "eval compare warning: --treatment prompt_cache is a no-op on the "
            "open-weight OpenRouter preset (non-Anthropic); both sides are "
            "identical. Use --treatment tool_concurrency for an active axis."
        )

    try:
        toolset = build_cli_toolset(tool_config_from_args(args))
    except tool_error as exc:
        print(f"eval compare error: {exc}")
        return 2

    def _agent(*, treatment: bool):
        return create_agent(
            provider=_provider(), tools=toolset, config=config_for(treatment)
        )

    report = await run_comparison(
        BatchRunner(_agent(treatment=False), concurrency=int(args.concurrency)),
        BatchRunner(_agent(treatment=True), concurrency=int(args.concurrency)),
        general_task_suite(),
        repeats=int(args.repeats),
        baseline_label=baseline_label,
        treatment_label=treatment_label,
        max_total_cost_usd=(
            float(args.max_cost_usd) if args.max_cost_usd is not None else None
        ),
    )
    print(render_comparison(report))
    print(
        json.dumps(
            {
                "axis": axis,
                "tier": tier,
                "repeats": int(args.repeats),
                "offline": offline,
                "success_rate_delta": report.success_rate_delta,
                "cost_usd_median_delta": report.cost_usd_median_delta,
                "latency_ms_median_delta": report.latency_ms_median_delta,
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
        print(
            "eval inspect error: pass exactly one of --summary-json or --artifact-json"
        )
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
        rows = [
            item
            for item in rows
            if isinstance(item, dict) and item.get("scenario_id") == args.scenario_id
        ]
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


__all__ = ["eval_compare_command", "eval_inspect_command", "eval_run_command"]
