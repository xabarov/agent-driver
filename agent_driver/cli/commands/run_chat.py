"""Run/chat command handlers extracted from CLI main."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections.abc import Callable
from pathlib import Path

from agent_driver.contracts import AgentRunInput
from agent_driver.cli.commands.common import (
    build_provider_and_toolset,
    print_provider_health,
)
from agent_driver.memory import SqliteMemoryStore, StoreBackedMemoryProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
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


def _memory_provider_from_args(args: argparse.Namespace) -> object | None:
    """Build a long-term memory provider from --memory / --memory-path, if any."""
    if str(getattr(args, "memory", "none")) != "sqlite":
        return None
    path = str(getattr(args, "memory_path", None) or ".agent-driver/memory.db")
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    return StoreBackedMemoryProvider(SqliteMemoryStore(path=path))


def _permission_gate_from_args(args: argparse.Namespace) -> object | None:
    """Build a permission ToolGate from --permission-mode (None when yolo)."""
    mode = str(getattr(args, "permission_mode", "yolo"))
    if mode == "yolo":
        return None  # allow-all: no gate needed
    return build_permission_gate(PermissionPolicy(mode=PermissionMode(mode)))


def _resolve_workspace_arg(args: argparse.Namespace) -> str | None:
    raw = getattr(args, "workspace", None)
    if raw is None or not str(raw).strip():
        return None
    path = Path(str(raw)).expanduser().resolve()
    if not path.exists() or not path.is_dir():
        raise ValueError(f"--workspace must be an existing directory: {path}")
    return str(path)


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
        memory_provider=_memory_provider_from_args(args),
        config=RunnerConfig(
            python_tool=_python_settings_from_args(args),
            enable_prompt_cache=bool(getattr(args, "prompt_cache", False)),
        ),
    )
    tool_gate = _permission_gate_from_args(args)
    run_id = args.run_id or f"run_{uuid.uuid4().hex[:12]}"
    app_metadata: dict[str, object] = {
        "stream_poll_interval_ms": args.stream_poll_interval_ms,
        "debug_tool_protocol": args.debug_tool_protocol,
    }
    try:
        workspace_cwd = _resolve_workspace_arg(args)
    except ValueError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2
    if workspace_cwd is not None:
        app_metadata["workspace_cwd"] = workspace_cwd
    run_input = AgentRunInput(
        input=args.prompt,
        run_id=run_id,
        agent_id=args.agent_id,
        graph_preset=args.graph_preset,
        stream=True,
        max_steps=args.max_steps,
        max_tool_calls=args.max_tool_calls,
        deadline_seconds=args.deadline_seconds,
        app_metadata=app_metadata,
    )
    async for line in cli_run_live_lines(
        agent.stream(run_input, tool_gate=tool_gate), prefer_rich=prefer_rich(args)
    ):
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
        memory_provider=_memory_provider_from_args(args),
        config=RunnerConfig(
            enable_compaction=True,
            enable_session_memory_compaction=True,
            python_tool=_python_settings_from_args(args),
            include_planning_prompt="todo_write" in tool_names,
            enable_prompt_cache=bool(getattr(args, "prompt_cache", False)),
        ),
    )
    tool_gate = _permission_gate_from_args(args)
    ui_mode = "rich" if (not args.plain and (args.rich or sys.stdout.isatty())) else "plain"
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    try:
        workspace_cwd = _resolve_workspace_arg(args)
    except ValueError as exc:
        print(f"workspace error: {exc}", file=sys.stderr)
        return 2
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
        workspace_cwd=workspace_cwd,
        tool_gate=tool_gate,
    )


__all__ = ["chat_command", "run_command"]
