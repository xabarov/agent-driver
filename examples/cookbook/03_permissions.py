"""Permissions: a permission gate denies a dangerous shell command.

Compose a ``PermissionPolicy`` into a ``ToolGate`` and pass it to the run. A
CRITICAL command (``rm -rf /``) is denied before the tool executes.

    python examples/cookbook/03_permissions.py
"""

from __future__ import annotations

import asyncio

from agent_driver.permissions import (
    CommandRiskLevel,
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
    classify_command,
)
from agent_driver.runtime.tool_gate import ToolGateContext, ToolGateDeny


async def main() -> None:
    # The classifier is reusable on its own.
    risk = classify_command("rm -rf /")
    print("rm -rf / =>", risk.level.name, risk.reasons)
    assert risk.level is CommandRiskLevel.CRITICAL

    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))
    context = ToolGateContext(
        tool_name="bash",
        args={"command": "rm -rf /"},
        run_id="r1",
        thread_id="t1",
        agent_id="agent",
        risk="high",
        side_effect="external",
        current_tool_calls=0,
    )
    decision = await gate(context)
    print("gate decision:", type(decision).__name__)
    assert isinstance(decision, ToolGateDeny)
    # Pass `gate` as `agent.run(..., tool_gate=gate)` to enforce it on a run.


if __name__ == "__main__":
    asyncio.run(main())
