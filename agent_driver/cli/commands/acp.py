"""`agent-driver acp` command — serve an agent over the Agent Client Protocol.

Builds an agent from the shared provider/tool/store/permission options (the
same wiring `chat` uses) and serves it over ACP on stdio. The ACP adapter and
its ``agent-client-protocol`` dependency are imported lazily so neither the
core nor the rest of the CLI requires the optional ``[acp]`` extra.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable

from agent_driver.cli.commands.common import (
    build_provider_and_toolset,
    print_provider_health,
)
from agent_driver.cli.commands.run_chat import (
    _memory_provider_from_args,
    _permission_gate_from_args,
    _python_settings_from_args,
)
from agent_driver.runtime import RunnerConfig


async def acp_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Callable[[argparse.Namespace], object],
    provider_config_from_args: Callable[[argparse.Namespace], object],
    tool_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    build_cli_provider: Callable[[object], object],
    build_cli_toolset: Callable[[object], object],
    create_agent: Callable[..., object],
    provider_error: type[Exception],
    tool_error: type[Exception],
) -> int:
    """Handle `agent-driver acp` — serve over ACP on stdio."""
    try:
        from agent_driver.adapters.acp import serve_acp_async
    except ImportError:
        print(
            "ACP support requires the optional dependency. Install it with:\n"
            "    pip install 'agent-driver[acp]'",
            file=sys.stderr,
        )
        return 2

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
        tool_gate=_permission_gate_from_args(args),
        agent_id=args.agent_id,
        graph_preset=args.graph_preset,
        config=RunnerConfig(
            python_tool=_python_settings_from_args(args),
            include_planning_prompt="todo_write" in tool_names,
            enable_prompt_cache=bool(getattr(args, "prompt_cache", False)),
        ),
    )

    await serve_acp_async(
        agent,
        name=args.acp_name,
        version=args.acp_version,
        use_unstable_protocol=bool(getattr(args, "acp_unstable", False)),
    )
    return 0


__all__ = ["acp_command"]
