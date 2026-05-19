"""Console CLI for run/replay/tail/tree workflows."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
from pathlib import Path
import sys
import uuid

from agent_driver.adapters import (
    cli_follow_lines,
    cli_replay_lines,
    cli_run_live_lines,
    cli_tail_lines,
    cli_tree_lines,
    is_rich_available,
)
from agent_driver.contracts import AgentRunInput
from agent_driver.cli.chat import run_chat_session
from agent_driver.cli.config import (
    config_to_dict,
    load_cli_config,
    resolve_with_env,
)
from agent_driver.cli.evals import (
    LiveEvalSkipped,
    EvalSummary,
    default_live_scenarios,
    render_eval_inspect,
    render_eval_timeline,
    run_live_evaluation,
)
from agent_driver.cli.providers import (
    CliProviderConfig,
    CliProviderConfigError,
    build_cli_provider,
)
from agent_driver.cli.sessions import SessionStore
from agent_driver.cli.tools import CliToolConfig, CliToolConfigError, build_cli_toolset
from agent_driver.runtime import RuntimeStoreFactoryConfig, create_runtime_store_bundle
from agent_driver.sdk import create_agent

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


def _add_provider_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("fake", "openrouter", "vllm", "ollama"),
        default="fake",
        help="Provider backend for run/chat execution.",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model name override for selected provider.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="Provider base URL override.",
    )
    parser.add_argument(
        "--api-key",
        default=None,
        help="Provider API key (prefer env variables in real usage).",
    )
    parser.add_argument(
        "--timeout-s",
        type=float,
        default=30.0,
        help="Provider request timeout in seconds.",
    )
    parser.add_argument(
        "--provider-healthcheck",
        action="store_true",
        help="Run provider healthcheck before starting run/chat.",
    )
    parser.add_argument(
        "--fake-response",
        default="ok",
        help="Fake provider response text for deterministic local runs.",
    )


def _add_tool_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--tools",
        choices=("default", "none", "all"),
        default="default",
        help="Tool surface mode for model-visible and executable tools.",
    )
    parser.add_argument(
        "--tool",
        action="append",
        default=[],
        help="Exact tool name to include (repeatable).",
    )
    parser.add_argument(
        "--tool-pack",
        action="append",
        default=[],
        help="Built-in tool pack to include (repeatable).",
    )
    parser.add_argument(
        "--max-tool-risk",
        choices=("low", "medium", "high"),
        default=None,
        help="Maximum allowed tool risk for selected surface.",
    )
    parser.add_argument(
        "--allow-dangerous-tools",
        action="store_true",
        help="Allow dangerous tool packs such as shell/filesystem_write.",
    )


def _add_store_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--store-kind",
        choices=("memory", "sqlite", "postgres"),
        default="memory",
        help="Runtime storage backend kind.",
    )
    parser.add_argument(
        "--sqlite-path",
        default=None,
        help="SQLite file path when --store-kind=sqlite.",
    )
    parser.add_argument(
        "--postgres-dsn",
        default=None,
        help="PostgreSQL DSN when --store-kind=postgres.",
    )
    parser.add_argument(
        "--postgres-schema",
        default="public",
        help="PostgreSQL schema for runtime rows.",
    )
    parser.add_argument(
        "--postgres-auto-create-schema",
        dest="postgres_auto_create_schema",
        action="store_true",
        default=True,
        help="Auto-create schema for postgres backend (default).",
    )
    parser.add_argument(
        "--no-postgres-auto-create-schema",
        dest="postgres_auto_create_schema",
        action="store_false",
        help="Disable schema auto-creation for postgres backend.",
    )


def _add_runtime_bounds_options(
    parser: argparse.ArgumentParser,
    *,
    default_max_steps: int | None,
    default_max_tool_calls: int | None,
    default_deadline_seconds: float | None,
) -> None:
    parser.add_argument(
        "--max-steps",
        type=int,
        default=default_max_steps,
        help="Maximum runtime step budget before run fails.",
    )
    parser.add_argument(
        "--max-tool-calls",
        type=int,
        default=default_max_tool_calls,
        help="Maximum executed tool calls before run fails.",
    )
    parser.add_argument(
        "--deadline-seconds",
        type=float,
        default=default_deadline_seconds,
        help="Wall-clock deadline for one run.",
    )
    parser.add_argument(
        "--debug-tool-protocol",
        action="store_true",
        help="Emit sanitized tool-protocol debug events in runtime stream.",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-driver",
        description="agent-driver CLI for run/replay/tail/tree/chat/ops.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run", help="Execute one run and print stream lines.")
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
    _add_runtime_bounds_options(
        run_parser,
        default_max_steps=12,
        default_max_tool_calls=6,
        default_deadline_seconds=90.0,
    )
    _add_provider_options(run_parser)
    _add_tool_options(run_parser)
    _add_store_options(run_parser)

    replay_parser = subparsers.add_parser("replay", help="Replay all events for one run id.")
    replay_parser.add_argument("--run-id", required=True, help="Run identifier to replay.")
    _add_store_options(replay_parser)

    tail_parser = subparsers.add_parser("tail", help="Show tail of run events.")
    tail_parser.add_argument("--run-id", required=True, help="Run identifier to inspect.")
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
        default=200,
        help="Polling interval used with --follow.",
    )
    _add_store_options(tail_parser)

    tree_parser = subparsers.add_parser("tree", help="Show event-kind counts for one run.")
    tree_parser.add_argument("--run-id", required=True, help="Run identifier to inspect.")
    _add_store_options(tree_parser)

    chat_parser = subparsers.add_parser("chat", help="Start interactive terminal chat.")
    chat_parser.add_argument("--agent-id", default="agent.cli", help="Agent identifier.")
    chat_parser.add_argument(
        "--graph-preset",
        default="single_react",
        help="Graph preset passed into AgentRunInput.",
    )
    chat_parser.add_argument(
        "--rich",
        action="store_true",
        help="Reserved for rich chat UX mode.",
    )
    chat_parser.add_argument(
        "--plain",
        action="store_true",
        help="Reserved for deterministic plain chat UX mode.",
    )
    chat_parser.add_argument(
        "--stream-poll-interval-ms",
        type=int,
        default=20,
        help="Polling interval for incremental stream projection.",
    )
    chat_parser.add_argument(
        "--resume-session",
        default=None,
        help="Resume existing local chat session by session id.",
    )
    _add_runtime_bounds_options(
        chat_parser,
        default_max_steps=8,
        default_max_tool_calls=4,
        default_deadline_seconds=60.0,
    )
    _add_provider_options(chat_parser)
    _add_tool_options(chat_parser)
    _add_store_options(chat_parser)

    config_parser = subparsers.add_parser("config", help="Show resolved CLI config.")
    config_sub = config_parser.add_subparsers(dest="config_command", required=True)
    config_sub.add_parser("show", help="Print resolved config as JSON.")

    doctor_parser = subparsers.add_parser("doctor", help="Provider and CLI diagnostics.")
    _add_provider_options(doctor_parser)
    _add_runtime_bounds_options(
        doctor_parser,
        default_max_steps=8,
        default_max_tool_calls=4,
        default_deadline_seconds=30.0,
    )
    _add_store_options(doctor_parser)
    doctor_parser.add_argument(
        "--live-check",
        action="store_true",
        help="Run live provider health check when credentials are configured.",
    )

    inspect_parser = subparsers.add_parser("inspect", help="Inspect one run in text/json.")
    inspect_parser.add_argument("--run-id", required=True, help="Run identifier to inspect.")
    inspect_parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Inspect output format.",
    )
    _add_store_options(inspect_parser)

    export_parser = subparsers.add_parser("export", help="Export one run as jsonl/markdown.")
    export_parser.add_argument("--run-id", required=True, help="Run identifier to export.")
    export_parser.add_argument(
        "--format",
        choices=("jsonl", "markdown"),
        default="jsonl",
        help="Export format.",
    )
    export_parser.add_argument("--output", required=True, help="Output file path.")
    _add_store_options(export_parser)

    sessions_parser = subparsers.add_parser("sessions", help="List/show saved chat sessions.")
    sessions_sub = sessions_parser.add_subparsers(dest="sessions_command", required=True)
    sessions_sub.add_parser("list", help="List known sessions.")
    sessions_show = sessions_sub.add_parser("show", help="Show one session.")
    sessions_show.add_argument("--session-id", required=True, help="Session identifier.")

    resume_parser = subparsers.add_parser("resume", help="Resume pending interrupt command.")
    resume_parser.add_argument(
        "action",
        choices=("approve", "reject", "edit", "cancel", "clarify"),
        help="Resume action.",
    )
    resume_parser.add_argument("--run-id", required=True, help="Run identifier.")
    resume_parser.add_argument("--interrupt-id", required=True, help="Interrupt identifier.")
    resume_parser.add_argument("--message", default=None, help="Optional message for action.")
    resume_parser.add_argument(
        "--edited-tool-args",
        default=None,
        help="JSON object for edited tool args (used by edit action).",
    )
    resume_parser.add_argument("--agent-id", default="agent.cli", help="Agent identifier.")
    resume_parser.add_argument(
        "--graph-preset", default="single_react", help="Graph preset for resume call."
    )
    _add_provider_options(resume_parser)
    _add_tool_options(resume_parser)
    _add_store_options(resume_parser)

    eval_parser = subparsers.add_parser("eval", help="Live evaluation harness commands.")
    eval_sub = eval_parser.add_subparsers(dest="eval_command", required=True)
    eval_run = eval_sub.add_parser("run", help="Run live/offline CLI evaluation scenarios.")
    _add_provider_options(eval_run)
    _add_tool_options(eval_run)
    _add_store_options(eval_run)
    eval_run.add_argument(
        "--output-dir",
        default=".agent-driver/evals",
        help="Base directory for evaluation artifact bundles.",
    )
    eval_run.add_argument(
        "--offline",
        action="store_true",
        help="Allow running eval harness without AGENT_DRIVER_RUN_LIVE_CLI_EVALS=1.",
    )
    eval_inspect = eval_sub.add_parser("inspect", help="Inspect one eval summary JSON row.")
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


def _store_config_from_args(args: argparse.Namespace) -> RuntimeStoreFactoryConfig:
    return RuntimeStoreFactoryConfig(
        kind=args.store_kind,
        sqlite_path=args.sqlite_path,
        postgres_dsn=args.postgres_dsn,
        postgres_schema=args.postgres_schema,
        postgres_auto_create_schema=args.postgres_auto_create_schema,
    )


def _print_lines(lines: Sequence[str]) -> int:
    for line in lines:
        print(line)
    return 0


def _prefer_rich(args: argparse.Namespace) -> bool:
    if args.plain:
        return False
    if args.rich:
        return True
    return is_rich_available()


def _provider_config_from_args(args: argparse.Namespace) -> CliProviderConfig:
    return CliProviderConfig(
        provider=args.provider,
        model=args.model,
        base_url=args.base_url,
        api_key=args.api_key,
        timeout_s=args.timeout_s,
        fake_response=args.fake_response,
    )


def _tool_config_from_args(args: argparse.Namespace) -> CliToolConfig:
    return CliToolConfig(
        tools_mode=args.tools,
        tools=tuple(args.tool),
        tool_packs=tuple(args.tool_pack),
        max_tool_risk=args.max_tool_risk,
        allow_dangerous_tools=args.allow_dangerous_tools,
    )


async def _run_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    try:
        provider = build_cli_provider(_provider_config_from_args(args))
    except CliProviderConfigError as exc:
        print(f"provider error: {exc}")
        return 2
    try:
        toolset = build_cli_toolset(_tool_config_from_args(args))
    except CliToolConfigError as exc:
        print(f"tools error: {exc}")
        return 2
    if args.provider_healthcheck:
        status = await provider.healthcheck()
        print(
            "provider> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}"
        )
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
    )
    run_id = args.run_id or f"run_{uuid.uuid4().hex[:12]}"
    run_input = AgentRunInput(
        input=args.prompt,
        run_id=run_id,
        agent_id=args.agent_id,
        graph_preset=args.graph_preset,
        stream=True,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        deadline_seconds=args.deadline_seconds,
        app_metadata={
            "stream_poll_interval_ms": args.stream_poll_interval_ms,
            "debug_tool_protocol": args.debug_tool_protocol,
        },
    )
    prefer_rich = _prefer_rich(args)
    async for line in cli_run_live_lines(agent.stream(run_input), prefer_rich=prefer_rich):
        print(line)
    print(json.dumps({"run_id": run_id, "store_kind": args.store_kind}, ensure_ascii=True))
    return 0


async def _chat_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    try:
        provider = build_cli_provider(_provider_config_from_args(args))
    except CliProviderConfigError as exc:
        print(f"provider error: {exc}")
        return 2
    try:
        toolset = build_cli_toolset(_tool_config_from_args(args))
    except CliToolConfigError as exc:
        print(f"tools error: {exc}")
        return 2
    if args.provider_healthcheck:
        status = await provider.healthcheck()
        print(
            "provider> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}"
        )
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
    )
    animate = not args.plain and (args.rich or sys.stdout.isatty())
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    return await run_chat_session(
        agent=agent,
        event_log=bundle.event_log,
        agent_id=args.agent_id,
        graph_preset=args.graph_preset,
        stream_poll_interval_ms=args.stream_poll_interval_ms,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        deadline_seconds=args.deadline_seconds,
        debug_tool_protocol=args.debug_tool_protocol,
        resume_session_id=getattr(args, "resume_session", None),
        provider_name=provider.name,
        model_name=getattr(provider, "_model", None),
        selected_manifests=selected_manifests,
        animate=animate,
    )


def _replay_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    return _print_lines(cli_replay_lines(bundle.event_log, run_id=args.run_id))


async def _tail_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    lines = cli_tail_lines(bundle.event_log, run_id=args.run_id, last_n=args.last_n)
    for line in lines:
        print(line)
    if not args.follow:
        return 0
    existing = bundle.event_log.list_for_run(args.run_id)
    if any(event.type.value in _TERMINAL_EVENTS for event in existing):
        return 0
    last_seq = max((event.seq for event in existing), default=0)
    async for line in cli_follow_lines(
        bundle.event_log,
        run_id=args.run_id,
        after_seq=last_seq,
        poll_interval_ms=args.poll_interval_ms,
        stop_on_terminal=True,
    ):
        print(line)
    return 0


def _tree_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    return _print_lines(cli_tree_lines(bundle.event_log, run_id=args.run_id))


def _config_show_command(_args: argparse.Namespace) -> int:
    config = resolve_with_env(load_cli_config())
    print(json.dumps(config_to_dict(config), ensure_ascii=True, indent=2))
    return 0


async def _doctor_command(args: argparse.Namespace) -> int:
    config = resolve_with_env(load_cli_config())
    print(json.dumps({"config": config_to_dict(config)}, ensure_ascii=True))
    try:
        provider = build_cli_provider(_provider_config_from_args(args))
    except CliProviderConfigError as exc:
        print(f"doctor> provider_error={exc}")
        return 2
    status = await provider.healthcheck()
    print(
        "doctor> "
        f"name={status.provider_name} healthy={status.healthy} "
        f"configured={status.configured} latency_ms={status.latency_ms}"
    )
    if args.live_check:
        run_input = AgentRunInput(
            input="doctor live check",
            run_id=f"run_doctor_{uuid.uuid4().hex[:10]}",
            agent_id=args.agent_id if hasattr(args, "agent_id") else "agent.cli",
            graph_preset=args.graph_preset if hasattr(args, "graph_preset") else "single_react",
            stream=False,
            max_steps=args.max_steps,
            max_tool_calls=args.max_tool_calls,
            deadline_seconds=args.deadline_seconds,
        )
        bundle = create_runtime_store_bundle(_store_config_from_args(args))
        agent = create_agent(
            provider=provider,
            checkpoint_store=bundle.checkpoint_store,
            event_log=bundle.event_log,
        )
        output = await agent.run(run_input)
        print(
            "doctor> "
            f"live_run_status={output.status.value} terminal_reason={output.terminal_reason.value if output.terminal_reason else None}"
        )
    return 0


def _inspect_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    events = bundle.event_log.list_for_run(args.run_id)
    if args.format == "json":
        payload = [item.model_dump(mode="json") for item in events]
        print(json.dumps(payload, ensure_ascii=True))
        return 0
    lines = cli_replay_lines(bundle.event_log, run_id=args.run_id)
    return _print_lines(lines)


def _export_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    events = bundle.event_log.list_for_run(args.run_id)
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    if args.format == "jsonl":
        rows = [json.dumps(item.model_dump(mode="json"), ensure_ascii=True) for item in events]
        output_path.write_text("\n".join(rows), encoding="utf-8")
    else:
        rows = [f"# Run {args.run_id}", ""]
        for line in cli_replay_lines(bundle.event_log, run_id=args.run_id):
            rows.append(f"- {line}")
        output_path.write_text("\n".join(rows), encoding="utf-8")
    print(f"export> {output_path}")
    return 0


def _sessions_command(args: argparse.Namespace) -> int:
    store = SessionStore()
    if args.sessions_command == "list":
        sessions = store.list_sessions()
        if not sessions:
            print("session> none")
            return 0
        for item in sessions:
            print(
                f"session> {item.session_id} thread={item.thread_id} runs={len(item.run_ids)} updated_at={item.updated_at}"
            )
        return 0
    record = store.get(args.session_id)
    if record is None:
        print(f"session> not_found {args.session_id}")
        return 2
    print(
        json.dumps(
            {
                "session_id": record.session_id,
                "thread_id": record.thread_id,
                "run_ids": list(record.run_ids),
                "transcript": [list(item) for item in record.transcript],
                "created_at": record.created_at,
                "updated_at": record.updated_at,
            },
            ensure_ascii=True,
            indent=2,
        )
    )
    return 0


async def _resume_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    try:
        provider = build_cli_provider(_provider_config_from_args(args))
    except CliProviderConfigError as exc:
        print(f"provider error: {exc}")
        return 2
    try:
        toolset = build_cli_toolset(_tool_config_from_args(args))
    except CliToolConfigError as exc:
        print(f"tools error: {exc}")
        return 2
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
    )
    if args.action == "approve":
        output = await agent.approve(run_id=args.run_id, interrupt_id=args.interrupt_id)
    elif args.action == "reject":
        output = await agent.reject(
            run_id=args.run_id, interrupt_id=args.interrupt_id, message=args.message
        )
    elif args.action == "cancel":
        output = await agent.cancel(run_id=args.run_id, interrupt_id=args.interrupt_id)
    elif args.action == "clarify":
        if not args.message:
            print("resume error: --message is required for clarify")
            return 2
        output = await agent.clarify(
            run_id=args.run_id, interrupt_id=args.interrupt_id, message=args.message
        )
    else:
        if not args.edited_tool_args:
            print("resume error: --edited-tool-args JSON is required for edit")
            return 2
        try:
            edited = json.loads(args.edited_tool_args)
        except json.JSONDecodeError:
            print("resume error: --edited-tool-args must be valid JSON object")
            return 2
        if not isinstance(edited, dict):
            print("resume error: --edited-tool-args must be JSON object")
            return 2
        output = await agent.edit(
            run_id=args.run_id,
            interrupt_id=args.interrupt_id,
            edited_tool_args=edited,
        )
    print(
        json.dumps(
            {
                "run_id": output.run_id,
                "status": output.status.value,
                "terminal_reason": output.terminal_reason.value if output.terminal_reason else None,
            },
            ensure_ascii=True,
        )
    )
    return 0


async def _eval_run_command(args: argparse.Namespace) -> int:
    try:
        bundle_dir, summaries = await run_live_evaluation(
            provider_config=_provider_config_from_args(args),
            tool_config=_tool_config_from_args(args),
            store_config=_store_config_from_args(args),
            output_dir=Path(args.output_dir),
            scenarios=default_live_scenarios(),
            offline=args.offline,
        )
    except LiveEvalSkipped as exc:
        print(f"eval skip: {exc}")
        return 0
    except (CliProviderConfigError, CliToolConfigError, RuntimeError) as exc:
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


def _eval_inspect_command(args: argparse.Namespace) -> int:
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


def _resolve_args_with_config(args: argparse.Namespace) -> argparse.Namespace:
    return _resolve_args_with_config_and_explicit(args, explicit_options=set())


def _resolve_args_with_config_and_explicit(
    args: argparse.Namespace, *, explicit_options: set[str]
) -> argparse.Namespace:
    config = resolve_with_env(load_cli_config())
    mapping = {
        "provider": config.provider,
        "model": config.model,
        "base_url": config.base_url,
        "timeout_s": config.timeout_s,
        "tools": config.tools,
        "max_steps": config.max_steps,
        "max_tool_calls": config.max_tool_calls,
        "deadline_seconds": config.deadline_seconds,
        "store_kind": config.store_kind,
        "sqlite_path": config.sqlite_path,
        "postgres_dsn": config.postgres_dsn,
    }
    option_names = {
        "provider": {"--provider"},
        "model": {"--model"},
        "base_url": {"--base-url"},
        "timeout_s": {"--timeout-s"},
        "tools": {"--tools"},
        "max_steps": {"--max-steps"},
        "max_tool_calls": {"--max-tool-calls"},
        "deadline_seconds": {"--deadline-seconds"},
        "store_kind": {"--store-kind"},
        "sqlite_path": {"--sqlite-path"},
        "postgres_dsn": {"--postgres-dsn"},
    }
    for key, value in mapping.items():
        if value is None or not hasattr(args, key):
            continue
        if option_names.get(key, set()).intersection(explicit_options):
            continue
        current = getattr(args, key)
        if current is None:
            setattr(args, key, value)
            continue
        default_sentinel = _default_sentinel_for_option(args, key)
        if default_sentinel is not None and current == default_sentinel:
            setattr(args, key, value)
    return args


def _default_sentinel_for_option(args: argparse.Namespace, key: str) -> object | None:
    command = str(getattr(args, "command", ""))
    if key == "provider":
        return "fake"
    if key == "timeout_s":
        return 30.0
    if key == "tools":
        return "default"
    if key == "store_kind":
        return "memory"
    if key == "max_steps":
        if command in {"chat", "doctor"}:
            return 8
        if command == "run":
            return 12
        return None
    if key == "max_tool_calls":
        if command in {"chat", "doctor"}:
            return 4
        if command == "run":
            return 6
        return None
    if key == "deadline_seconds":
        if command in {"chat", "doctor"}:
            return 60.0 if command == "chat" else 30.0
        if command == "run":
            return 90.0
        return None
    return None


def _extract_explicit_options(argv: Sequence[str] | None) -> set[str]:
    items = list(argv) if argv is not None else list(sys.argv[1:])
    explicit: set[str] = set()
    for token in items:
        if token.startswith("--"):
            explicit.add(token)
    return explicit


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for agent-driver product commands."""
    parser = _build_parser()
    parsed = parser.parse_args(argv)
    args = _resolve_args_with_config_and_explicit(
        parsed, explicit_options=_extract_explicit_options(argv)
    )
    if args.command == "run":
        try:
            return asyncio.run(_run_command(args))
        except KeyboardInterrupt:
            print("run> interrupted")
            return 130
    if args.command == "chat":
        try:
            return asyncio.run(_chat_command(args))
        except KeyboardInterrupt:
            print("chat> interrupted")
            return 130
    if args.command == "replay":
        return _replay_command(args)
    if args.command == "tail":
        try:
            return asyncio.run(_tail_command(args))
        except KeyboardInterrupt:
            print("tail> interrupted")
            return 130
    if args.command == "tree":
        return _tree_command(args)
    if args.command == "config":
        if args.config_command == "show":
            return _config_show_command(args)
    if args.command == "doctor":
        try:
            return asyncio.run(_doctor_command(args))
        except KeyboardInterrupt:
            print("doctor> interrupted")
            return 130
    if args.command == "inspect":
        return _inspect_command(args)
    if args.command == "export":
        return _export_command(args)
    if args.command == "sessions":
        return _sessions_command(args)
    if args.command == "resume":
        try:
            return asyncio.run(_resume_command(args))
        except KeyboardInterrupt:
            print("resume> interrupted")
            return 130
    if args.command == "eval":
        if args.eval_command == "run":
            try:
                return asyncio.run(_eval_run_command(args))
            except KeyboardInterrupt:
                print("eval> interrupted")
                return 130
        if args.eval_command == "inspect":
            return _eval_inspect_command(args)
    parser.error(f"Unsupported command '{args.command}'")
    return 2


if __name__ == "__main__":
    sys.exit(main())
