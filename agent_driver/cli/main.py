"""Console CLI for run/replay/tail/tree workflows."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
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
from agent_driver.cli.providers import (
    CliProviderConfig,
    CliProviderConfigError,
    build_cli_provider,
)
from agent_driver.runtime import RuntimeStoreFactoryConfig, create_runtime_store_bundle
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


def _add_provider_options(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--provider",
        choices=("fake", "openai-compatible", "ollama"),
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
        "--api-key-env",
        default=None,
        help="Environment variable name containing API key.",
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


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-driver",
        description="agent-driver CLI for run/replay/tail/tree.",
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
    _add_provider_options(run_parser)
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
    _add_provider_options(chat_parser)
    _add_store_options(chat_parser)
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
        api_key_env=args.api_key_env,
        timeout_s=args.timeout_s,
        fake_response=args.fake_response,
    )


async def _run_command(args: argparse.Namespace) -> int:
    bundle = create_runtime_store_bundle(_store_config_from_args(args))
    try:
        provider = build_cli_provider(_provider_config_from_args(args))
    except CliProviderConfigError as exc:
        print(f"provider error: {exc}")
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
        tools=ToolSet.only(),
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
        app_metadata={"stream_poll_interval_ms": args.stream_poll_interval_ms},
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
    if args.provider_healthcheck:
        status = await provider.healthcheck()
        print(
            "provider> "
            f"name={status.provider_name} healthy={status.healthy} "
            f"configured={status.configured} latency_ms={status.latency_ms}"
        )
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
    )
    _ = _prefer_rich(args)
    return await run_chat_session(
        agent=agent,
        event_log=bundle.event_log,
        agent_id=args.agent_id,
        graph_preset=args.graph_preset,
        stream_poll_interval_ms=args.stream_poll_interval_ms,
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


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entrypoint for agent-driver product commands."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    if args.command == "run":
        return asyncio.run(_run_command(args))
    if args.command == "chat":
        return asyncio.run(_chat_command(args))
    if args.command == "replay":
        return _replay_command(args)
    if args.command == "tail":
        return asyncio.run(_tail_command(args))
    if args.command == "tree":
        return _tree_command(args)
    parser.error(f"Unsupported command '{args.command}'")
    return 2


if __name__ == "__main__":
    sys.exit(main())
