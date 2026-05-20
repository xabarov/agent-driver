"""Run/chat command handlers extracted from CLI main."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable

from agent_driver.contracts import AgentRunInput
from agent_driver.cli.commands.common import (
    build_provider_and_toolset,
    print_provider_health,
)
from agent_driver.runtime import RunnerConfig
import os

from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.tools.builtin.python_imports import (
    parse_python_scientific_enabled,
    resolve_python_default_imports,
)


def _python_settings_from_args(args: argparse.Namespace) -> PythonToolSettings:
    raw_backend = str(getattr(args, "python_backend", None) or "local").strip()
    raw_imports = str(getattr(args, "python_allow_imports", None) or "").strip()
    extra_imports = tuple(
        item.strip() for item in raw_imports.split(",") if item.strip()
    )
    include_scientific = parse_python_scientific_enabled(
        no_python_scientific=bool(getattr(args, "no_python_scientific", False)),
        env_value=os.environ.get("AGENT_DRIVER_PYTHON_SCIENTIFIC"),
    )
    defaults = resolve_python_default_imports(include_scientific=include_scientific)
    merged_defaults = defaults + tuple(
        item for item in extra_imports if item not in set(defaults)
    )
    return PythonToolSettings(
        enabled=bool(getattr(args, "enable_python", False)),
        backend=raw_backend or "local",
        include_scientific_stack=include_scientific,
        default_imports=merged_defaults,
        allow_overlay=bool(extra_imports),
    )


async def run_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    provider_config_from_args: Callable[[argparse.Namespace], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    prefer_rich: Callable[[argparse.Namespace], bool],
    create_runtime_store_bundle: Callable[[object], object],
    build_cli_provider: Callable[[object], object],
    build_cli_toolset: Callable[[object], object],
    create_agent: Callable[..., object],
    cli_run_live_lines: Callable[..., object],
    provider_error: type[Exception],
    tool_error: type[Exception],
) -> int:
    """Handle `agent-driver run` command."""
    bundle = create_runtime_store_bundle(store_config_from_args(args))
    provider, toolset, error_code = build_provider_and_toolset(
        args,
        provider_config_from_args=provider_config_from_args,
        tool_config_from_args=tool_config_from_args,
        build_cli_provider=build_cli_provider,
        build_cli_toolset=build_cli_toolset,
        provider_error=provider_error,
        tool_error=tool_error,
    )
    if error_code is not None:
        return error_code
    if provider is None or toolset is None:
        return 2
    if args.provider_healthcheck:
        status = await provider.healthcheck()
        print_provider_health(status)
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
        config=RunnerConfig(python_tool=_python_settings_from_args(args)),
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
    async for line in cli_run_live_lines(agent.stream(run_input), prefer_rich=prefer_rich(args)):
        print(line)
    print(json.dumps({"run_id": run_id, "store_kind": args.store_kind}, ensure_ascii=True))
    return 0


async def chat_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    provider_config_from_args: Callable[[argparse.Namespace], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    build_cli_provider: Callable[[object], object],
    build_cli_toolset: Callable[[object], object],
    create_agent: Callable[..., object],
    run_chat_session: Callable[..., object],
    provider_error: type[Exception],
    tool_error: type[Exception],
) -> int:
    """Handle `agent-driver chat` command."""
    bundle = create_runtime_store_bundle(store_config_from_args(args))
    provider, toolset, error_code = build_provider_and_toolset(
        args,
        provider_config_from_args=provider_config_from_args,
        tool_config_from_args=tool_config_from_args,
        build_cli_provider=build_cli_provider,
        build_cli_toolset=build_cli_toolset,
        provider_error=provider_error,
        tool_error=tool_error,
    )
    if error_code is not None:
        return error_code
    if provider is None or toolset is None:
        return 2
    if args.provider_healthcheck:
        status = await provider.healthcheck()
        print_provider_health(status)
    tool_names = set(toolset.names or ())
    agent = create_agent(
        provider=provider,
        tools=toolset,
        checkpoint_store=bundle.checkpoint_store,
        event_log=bundle.event_log,
        config=RunnerConfig(
            enable_compaction=True,
            enable_session_memory_compaction=True,
            python_tool=_python_settings_from_args(args),
            include_planning_prompt="todo_write" in tool_names,
        ),
    )
    ui_mode = "rich" if (not args.plain and (args.rich or sys.stdout.isatty())) else "plain"
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
        ui_mode=ui_mode,
    )


__all__ = ["chat_command", "run_command"]
