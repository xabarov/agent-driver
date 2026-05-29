"""Replay/tail/tree/inspect/export/session command handlers."""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path

from agent_driver.cli.sessions import SessionStore

_TERMINAL_EVENTS = {"run_completed", "run_failed", "run_cancelled"}


def replay_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    cli_replay_lines: Callable[..., list[str]],
    print_lines: Callable[[list[str]], int],
) -> int:
    bundle = create_runtime_store_bundle(store_config_from_args(args))
    return print_lines(cli_replay_lines(bundle.event_log, run_id=args.run_id))


async def tail_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    cli_tail_lines: Callable[..., list[str]],
    cli_follow_lines: Callable[..., object],
) -> int:
    bundle = create_runtime_store_bundle(store_config_from_args(args))
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


def tree_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    cli_tree_lines: Callable[..., list[str]],
    print_lines: Callable[[list[str]], int],
) -> int:
    bundle = create_runtime_store_bundle(store_config_from_args(args))
    return print_lines(cli_tree_lines(bundle.event_log, run_id=args.run_id))


def inspect_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    cli_replay_lines: Callable[..., list[str]],
    print_lines: Callable[[list[str]], int],
) -> int:
    bundle = create_runtime_store_bundle(store_config_from_args(args))
    events = bundle.event_log.list_for_run(args.run_id)
    if args.format == "json":
        payload = [item.model_dump(mode="json") for item in events]
        print(json.dumps(payload, ensure_ascii=True))
        return 0
    lines = cli_replay_lines(bundle.event_log, run_id=args.run_id)
    return print_lines(lines)


def export_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    cli_replay_lines: Callable[..., list[str]],
) -> int:
    bundle = create_runtime_store_bundle(store_config_from_args(args))
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


def sessions_command(args: argparse.Namespace) -> int:
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


__all__ = [
    "export_command",
    "inspect_command",
    "replay_command",
    "sessions_command",
    "tail_command",
    "tree_command",
]
