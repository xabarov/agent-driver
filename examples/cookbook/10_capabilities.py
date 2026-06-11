"""Capabilities: wire prompt-cache + a permission gate once, not per call.

R1 grouped the opt-in capability knobs into ``CapabilitySettings`` (still
settable as flat ``RunnerConfig`` kwargs); R2 lets ``create_agent`` take a
default ``tool_gate`` so the permission gate is wired once and applies to every
turn. This shows the one-stop setup.

    python examples/cookbook/10_capabilities.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
)
from agent_driver.runtime import CapabilitySettings, RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


async def main() -> None:
    capabilities = CapabilitySettings(
        enable_prompt_cache=True,  # Anthropic prompt-cache breakpoints (no-op elsewhere)
        tool_concurrency_limit=4,  # cap parallel tool execution
    )
    # The permission gate is passed once at construction (R2): no need to thread
    # tool_gate through every run/stream/session call.
    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))

    agent = create_agent(
        provider=FakeProvider(response_text="all set"),
        tools=ToolSet.only(),
        config=RunnerConfig(capabilities=capabilities),
        tool_gate=gate,
    )
    # Flat kwargs are equivalent: RunnerConfig(enable_prompt_cache=True, tool_concurrency_limit=4)
    output = await agent.query("Say hello.", run_id="caps-1")
    print("answer:", output.answer)
    print("prompt_cache:", agent.runner.config.enable_prompt_cache)
    print("tool_concurrency:", agent.runner.config.tool_concurrency_limit)


if __name__ == "__main__":
    asyncio.run(main())
