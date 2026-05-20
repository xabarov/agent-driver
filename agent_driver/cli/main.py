"""Console CLI for run/replay/tail/tree workflows."""

from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
import json
import sys

from agent_driver.adapters import (
    cli_follow_lines,
    cli_replay_lines,
    cli_run_live_lines,
    cli_tail_lines,
    cli_tree_lines,
    is_rich_available,
)
from agent_driver.cli.chat import run_chat_session
from agent_driver.cli.config import (
    config_to_dict,
    load_cli_config,
    resolve_with_env,
)
from agent_driver.cli.evals import (
    LiveEvalSkipped,
    live_scenarios_for_suite,
    render_eval_inspect,
    render_eval_timeline,
    run_live_evaluation,
)
from agent_driver.cli.commands.evals import (
    eval_inspect_command as _eval_inspect_command_impl,
)
from agent_driver.cli.commands.evals import eval_run_command as _eval_run_command_impl
from agent_driver.cli.commands.ops import doctor_command as _doctor_command_impl
from agent_driver.cli.commands.ops import resume_command as _resume_command_impl
from agent_driver.cli.commands.runtime_views import (
    export_command as _export_command_impl,
)
from agent_driver.cli.commands.runtime_views import (
    inspect_command as _inspect_command_impl,
)
from agent_driver.cli.commands.runtime_views import (
    replay_command as _replay_command_impl,
)
from agent_driver.cli.commands.runtime_views import (
    sessions_command as _sessions_command_impl,
)
from agent_driver.cli.commands.runtime_views import tail_command as _tail_command_impl
from agent_driver.cli.commands.runtime_views import tree_command as _tree_command_impl
from agent_driver.cli.parser import build_parser as _build_parser_impl
from agent_driver.cli.providers import (
    CliProviderConfig,
    CliProviderConfigError,
    build_cli_provider,
)
from agent_driver.cli.tools import CliToolConfig, CliToolConfigError, build_cli_toolset
from agent_driver.runtime import RuntimeStoreFactoryConfig, create_runtime_store_bundle
from agent_driver.cli.commands.run_chat import chat_command as _chat_command_impl
from agent_driver.cli.commands.run_chat import run_command as _run_command_impl
from agent_driver.sdk import create_agent

def _build_parser() -> argparse.ArgumentParser:
    return _build_parser_impl()


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
        enable_python=bool(getattr(args, "enable_python", False)),
    )


async def _run_command(args: argparse.Namespace) -> int:
    return await _run_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        provider_config_from_args=_provider_config_from_args,
        tool_config_from_args=_tool_config_from_args,
        prefer_rich=_prefer_rich,
        create_runtime_store_bundle=create_runtime_store_bundle,
        build_cli_provider=build_cli_provider,
        build_cli_toolset=build_cli_toolset,
        create_agent=create_agent,
        cli_run_live_lines=cli_run_live_lines,
        provider_error=CliProviderConfigError,
        tool_error=CliToolConfigError,
    )


async def _chat_command(args: argparse.Namespace) -> int:
    return await _chat_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        provider_config_from_args=_provider_config_from_args,
        tool_config_from_args=_tool_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        build_cli_provider=build_cli_provider,
        build_cli_toolset=build_cli_toolset,
        create_agent=create_agent,
        run_chat_session=run_chat_session,
        provider_error=CliProviderConfigError,
        tool_error=CliToolConfigError,
    )


def _replay_command(args: argparse.Namespace) -> int:
    return _replay_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        cli_replay_lines=cli_replay_lines,
        print_lines=_print_lines,
    )


async def _tail_command(args: argparse.Namespace) -> int:
    return await _tail_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        cli_tail_lines=cli_tail_lines,
        cli_follow_lines=cli_follow_lines,
    )


def _tree_command(args: argparse.Namespace) -> int:
    return _tree_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        cli_tree_lines=cli_tree_lines,
        print_lines=_print_lines,
    )


def _config_show_command(_args: argparse.Namespace) -> int:
    config = resolve_with_env(load_cli_config())
    print(json.dumps(config_to_dict(config), ensure_ascii=True, indent=2))
    return 0


async def _doctor_command(args: argparse.Namespace) -> int:
    return await _doctor_command_impl(
        args,
        load_config=load_cli_config,
        resolve_with_env=resolve_with_env,
        config_to_dict=config_to_dict,
        provider_config_from_args=_provider_config_from_args,
        build_cli_provider=build_cli_provider,
        provider_error=CliProviderConfigError,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        create_agent=create_agent,
    )


def _inspect_command(args: argparse.Namespace) -> int:
    return _inspect_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        cli_replay_lines=cli_replay_lines,
        print_lines=_print_lines,
    )


def _export_command(args: argparse.Namespace) -> int:
    return _export_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        cli_replay_lines=cli_replay_lines,
    )


def _sessions_command(args: argparse.Namespace) -> int:
    return _sessions_command_impl(args)


async def _resume_command(args: argparse.Namespace) -> int:
    return await _resume_command_impl(
        args,
        store_config_from_args=_store_config_from_args,
        provider_config_from_args=_provider_config_from_args,
        tool_config_from_args=_tool_config_from_args,
        create_runtime_store_bundle=create_runtime_store_bundle,
        build_cli_provider=build_cli_provider,
        build_cli_toolset=build_cli_toolset,
        create_agent=create_agent,
        provider_error=CliProviderConfigError,
        tool_error=CliToolConfigError,
    )


async def _eval_run_command(args: argparse.Namespace) -> int:
    return await _eval_run_command_impl(
        args,
        scenarios_for_suite=live_scenarios_for_suite,
        run_live_evaluation=run_live_evaluation,
        provider_config_from_args=_provider_config_from_args,
        tool_config_from_args=_tool_config_from_args,
        store_config_from_args=_store_config_from_args,
        provider_error=CliProviderConfigError,
        tool_error=CliToolConfigError,
        live_eval_skipped=LiveEvalSkipped,
    )


def _eval_inspect_command(args: argparse.Namespace) -> int:
    return _eval_inspect_command_impl(
        args,
        render_eval_timeline=render_eval_timeline,
        render_eval_inspect=render_eval_inspect,
    )


def _resolve_args_with_config(args: argparse.Namespace) -> argparse.Namespace:
    return _resolve_args_with_config_and_explicit(args, explicit_options=set())


def _resolve_args_with_config_and_explicit(
    args: argparse.Namespace, *, explicit_options: set[str]
) -> argparse.Namespace:
    config = resolve_with_env(load_cli_config())
    command = str(getattr(args, "command", ""))
    eval_without_python_defaults = command == "eval"
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
        "enable_python": config.enable_python,
        "python_backend": config.python_backend,
        "python_allow_imports": config.python_allow_imports,
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
        "enable_python": {"--enable-python"},
        "python_backend": {"--python-backend"},
        "python_allow_imports": {"--python-allow-imports"},
    }
    for key, value in mapping.items():
        if eval_without_python_defaults and key in {
            "enable_python",
            "python_backend",
            "python_allow_imports",
        }:
            continue
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
    if key == "enable_python":
        return False
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
