"""Top-level parser builder for agent-driver CLI."""

from __future__ import annotations

import argparse

from agent_driver.cli.parser.options import (
    add_capability_options,
    add_provider_options,
    add_runtime_bounds_options,
    add_store_options,
    add_tool_options,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-driver",
        description="agent-driver CLI for run/replay/tail/tree/chat/ops.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser(
        "run", help="Execute one run and print stream lines."
    )
    run_parser.add_argument("prompt", help="User prompt text for one run.")
    run_parser.add_argument("--run-id", default=None, help="Optional run identifier.")
    run_parser.add_argument("--agent-id", default="agent.cli", help="Agent identifier.")
    run_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    run_parser.add_argument(
        "--rich",
        action="store_true",
        help="Force rich rendering when optional dependency is available.",
    )
    run_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich rendering and force deterministic plain text.",
    )
    run_parser.add_argument(
        "--stream-poll-interval-ms",
        type=int,
        default=20,
        help="Polling interval for incremental stream projection.",
    )
    run_parser.add_argument(
        "--workspace",
        default=None,
        help="Workspace directory for filesystem and shell tools.",
    )
    add_runtime_bounds_options(
        run_parser,
        default_max_steps=12,
        default_max_tool_calls=6,
        default_deadline_seconds=90.0,
    )
    add_provider_options(run_parser)
    add_tool_options(run_parser)
    add_store_options(run_parser)
    add_capability_options(run_parser)

    replay_parser = subparsers.add_parser(
        "replay", help="Replay all events for one run id."
    )
    replay_parser.add_argument(
        "--run-id", required=True, help="Run identifier to replay."
    )
    add_store_options(replay_parser)

    tail_parser = subparsers.add_parser("tail", help="Show tail of run events.")
    tail_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    tail_parser.add_argument(
        "--last-n", type=int, default=20, help="Number of trailing events."
    )
    tail_parser.add_argument(
        "--follow",
        action="store_true",
        help="Continue polling for new events after initial tail output.",
    )
    tail_parser.add_argument(
        "--poll-interval-ms",
        type=int,
        default=250,
        help="Follow polling interval in milliseconds.",
    )
    add_store_options(tail_parser)

    tree_parser = subparsers.add_parser("tree", help="Render step tree for one run id.")
    tree_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    add_store_options(tree_parser)

    chat_parser = subparsers.add_parser("chat", help="Interactive chat session.")
    chat_parser.add_argument(
        "--agent-id", default="agent.cli", help="Agent identifier."
    )
    chat_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    chat_parser.add_argument(
        "--plain",
        action="store_true",
        help="Disable rich rendering and force deterministic plain text.",
    )
    chat_parser.add_argument(
        "--rich",
        action="store_true",
        help="Force rich rendering when optional dependency is available.",
    )
    chat_parser.add_argument(
        "--stream-poll-interval-ms",
        type=int,
        default=20,
        help="Polling interval for incremental stream projection.",
    )
    chat_parser.add_argument(
        "--workspace",
        default=None,
        help="Initial workspace directory for filesystem and shell tools.",
    )
    add_runtime_bounds_options(
        chat_parser,
        default_max_steps=24,
        default_max_tool_calls=12,
        default_deadline_seconds=180.0,
    )
    add_provider_options(chat_parser)
    add_tool_options(chat_parser)
    add_store_options(chat_parser)
    add_capability_options(chat_parser)
    chat_parser.add_argument(
        "--resume-session",
        default=None,
        help="Resume from persisted local chat session id.",
    )

    config_parser = subparsers.add_parser("config", help="Configuration helpers.")
    config_subparsers = config_parser.add_subparsers(
        dest="config_command", required=True
    )
    config_subparsers.add_parser("show", help="Print resolved CLI config (JSON).")

    doctor_parser = subparsers.add_parser(
        "doctor", help="Diagnostics for config/provider/runtime."
    )
    doctor_parser.add_argument(
        "--agent-id", default="agent.cli", help="Agent identifier."
    )
    doctor_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset for optional live check.",
    )
    doctor_parser.add_argument(
        "--live-check",
        action="store_true",
        help="Run one short live check against configured provider/runtime.",
    )
    add_runtime_bounds_options(
        doctor_parser,
        default_max_steps=8,
        default_max_tool_calls=4,
        default_deadline_seconds=30.0,
    )
    add_provider_options(doctor_parser)
    add_tool_options(doctor_parser)
    add_store_options(doctor_parser)

    inspect_parser = subparsers.add_parser(
        "inspect", help="Inspect one run in text/json."
    )
    inspect_parser.add_argument(
        "--run-id", required=True, help="Run identifier to inspect."
    )
    inspect_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Inspect output format.",
    )
    add_store_options(inspect_parser)

    export_parser = subparsers.add_parser("export", help="Export one run to file.")
    export_parser.add_argument(
        "--run-id", required=True, help="Run identifier to export."
    )
    export_parser.add_argument(
        "--format",
        choices=("markdown", "jsonl"),
        default="markdown",
        help="Export file format.",
    )
    export_parser.add_argument(
        "--output",
        required=True,
        help="Output path for exported run artifact.",
    )
    add_store_options(export_parser)

    sessions_parser = subparsers.add_parser(
        "sessions", help="Manage local chat session metadata."
    )
    sessions_subparsers = sessions_parser.add_subparsers(
        dest="sessions_command", required=True
    )
    sessions_subparsers.add_parser("list", help="List local sessions.")
    sessions_show = sessions_subparsers.add_parser(
        "show", help="Show one session details."
    )
    sessions_show.add_argument(
        "--session-id", required=True, help="Session identifier."
    )

    resume_parser = subparsers.add_parser(
        "resume", help="Resume pending interrupt decisions."
    )
    resume_parser.add_argument(
        "action",
        choices=("approve", "reject", "cancel", "clarify", "edit"),
        help="Resume action to apply.",
    )
    resume_parser.add_argument("--run-id", required=True, help="Paused run identifier.")
    resume_parser.add_argument(
        "--interrupt-id",
        required=True,
        help="Interrupt identifier returned by paused run.",
    )
    resume_parser.add_argument(
        "--message",
        default=None,
        help="Optional message for reject/clarify actions.",
    )
    resume_parser.add_argument(
        "--edited-tool-args",
        default=None,
        help="JSON object for edit action tool args override.",
    )
    add_provider_options(resume_parser)
    add_tool_options(resume_parser)
    add_store_options(resume_parser)

    eval_parser = subparsers.add_parser(
        "eval", help="Run and inspect live CLI evaluation bundle."
    )
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser("run", help="Execute live eval scenarios.")
    eval_run.add_argument(
        "--output-dir",
        default=".agent-driver/evals",
        help="Directory to write eval artifacts.",
    )
    eval_run.add_argument(
        "--offline",
        action="store_true",
        help="Run deterministic offline mode with fake provider.",
    )
    eval_run.add_argument(
        "--allow-live-without-env",
        action="store_true",
        help="Allow running eval harness without AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1.",
    )
    eval_run.add_argument(
        "--continue-on-error",
        action="store_true",
        help="Continue suite after a scenario failure; write failures.json in bundle.",
    )
    eval_run.add_argument(
        "--suite",
        choices=("default", "default_smoke", "deep", "regression", "all"),
        default="default",
        help="Evaluation scenario suite.",
    )
    add_provider_options(eval_run)
    add_tool_options(eval_run)
    add_store_options(eval_run)
    eval_compare = eval_sub.add_parser(
        "compare",
        help="Baseline-vs-treatment harness comparison on the general suite.",
    )
    eval_compare.add_argument(
        "--treatment",
        choices=("prompt_cache", "tool_arg_truncation", "tool_concurrency"),
        default="prompt_cache",
        help="Harness axis to flip off vs on (one axis at a time).",
    )
    eval_compare.add_argument(
        "--tier",
        choices=("small", "mid", "large"),
        default="mid",
        help="Open-weight model tier (OpenRouter) for live runs.",
    )
    eval_compare.add_argument(
        "--repeats",
        type=int,
        default=5,
        help="Runs per task (N-run reliability; report median over N).",
    )
    eval_compare.add_argument(
        "--concurrency", type=int, default=4, help="Concurrent runs."
    )
    eval_compare.add_argument(
        "--max-cost-usd",
        type=float,
        default=5.0,
        help="Per-side suite spend ceiling; runs past it are skipped.",
    )
    eval_compare.add_argument(
        "--offline",
        action="store_true",
        help="Deterministic dry run with the fake provider (no network).",
    )
    add_tool_options(eval_compare)

    eval_inspect = eval_sub.add_parser(
        "inspect", help="Inspect one eval summary JSON row."
    )
    eval_inspect.add_argument(
        "--summary-json",
        default=None,
        help="Path to summary.json produced by eval run.",
    )
    eval_inspect.add_argument(
        "--artifact-json",
        default=None,
        help="Path to one scenario artifact json file produced by eval run.",
    )
    eval_inspect.add_argument(
        "--scenario-id",
        default=None,
        help="Optional scenario id filter for inspect.",
    )
    return parser


__all__ = ["build_parser"]
