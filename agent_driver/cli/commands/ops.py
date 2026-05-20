"""Doctor/resume command handlers."""

from __future__ import annotations

import argparse
import json
import uuid
from collections.abc import Callable

from agent_driver.cli.commands.common import (
    build_provider_and_toolset,
    print_provider_health,
)
from agent_driver.contracts import AgentRunInput


async def doctor_command(
    args: argparse.Namespace,
    *,
    load_config: Callable[[], object],
    resolve_with_env: Callable[[object], object],
    config_to_dict: Callable[[object], dict[str, object]],
    provider_config_from_args: Callable[[argparse.Namespace], object],
    build_cli_provider: Callable[[object], object],
    provider_error: type[Exception],
    store_config_from_args: Callable[[argparse.Namespace], object],
    create_runtime_store_bundle: Callable[[object], object],
    create_agent: Callable[..., object],
) -> int:
    config = resolve_with_env(load_config())
    print(json.dumps({"config": config_to_dict(config)}, ensure_ascii=True))
    try:
        provider = build_cli_provider(provider_config_from_args(args))
    except provider_error as exc:
        print(f"doctor> provider_error={exc}")
        return 2
    status = await provider.healthcheck()
    print_provider_health(status)
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
        bundle = create_runtime_store_bundle(store_config_from_args(args))
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


async def resume_command(
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


__all__ = ["doctor_command", "resume_command"]
