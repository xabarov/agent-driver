"""End-to-end embedded harness — the supported durable-embedding surface.

Assembles a run entirely from the documented public facades: a fake provider,
host-supplied checkpoint + event stores, a custom governed tool, a lifecycle
hook, an approval tool-gate, and a run abort handle. Exercises the full
pause → approve → resume path and a durable abort — using only
``agent_driver.sdk`` / ``.runtime`` / ``.llm`` / ``.contracts`` imports (never a
``runtime.single_agent.*`` / underscore internal).

python examples/cookbook/19_embedded_e2e.py
"""

from __future__ import annotations

import asyncio

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm import FakeProvider, LlmFinishReason
from agent_driver.runtime import (
    BaseRunLifecycleHook,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunAbortHandle,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateResult,
)
from agent_driver.sdk import ToolSet, create_agent

NOTES: list[str] = []


class _ScriptedProvider(FakeProvider):
    """First turn plans a ``write_note`` call; the next turn stops."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self._turn = 0

    async def complete(self, request):  # noqa: ANN001
        self._turn += 1
        response = await super().complete(request)  # a valid LlmResponse
        if self._turn != 1:
            return response
        planned = ToolCall(
            tool_name="write_note",
            tool_call_id="note_1",
            args={"text": "hello from the embedded host"},
        )
        return response.model_copy(
            update={
                "finish_reason": LlmFinishReason.TOOL_CALLS,
                "metadata": {
                    **response.metadata,
                    "planned_tool_calls": [planned.model_dump(mode="json")],
                },
            }
        )


class _AuditHook(BaseRunLifecycleHook):
    """A minimal host lifecycle hook — records when runs complete."""

    def __init__(self) -> None:
        self.completed: list[str] = []

    async def on_run_completed(self, context, *, answer: str) -> None:  # noqa: ANN001
        self.completed.append(context.run_id)


async def _approval_gate(ctx: ToolGateContext) -> ToolGateResult:
    """Pause the run for operator approval before any note is written."""
    if ctx.tool_name == "write_note":
        return ToolGateAsk(message="Approve writing a note?")
    return ToolGateAllow()


async def main() -> dict[str, object]:
    hook = _AuditHook()
    agent = create_agent(
        provider=_ScriptedProvider(),
        tools=ToolSet.only(),  # no builtins; we add one custom tool below
        checkpoint_store=InMemoryCheckpointStore(),  # host-owned durable store
        event_log=InMemoryEventLog(),
        lifecycle_hooks=(hook,),
        tool_gate=_approval_gate,
    )

    async def write_note(text: str) -> dict:
        NOTES.append(text)
        return {"summary": f"noted: {text}"}

    agent.add_tool(write_note, name="write_note", description="Append a note")

    # 1) Run pauses on the approval gate before the tool executes.
    paused = await agent.run(
        AgentRunInput(
            input="write a note",
            run_id="e2e",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert paused.status.value == "paused" and paused.interrupt is not None
    assert NOTES == []  # nothing written before approval

    # 2) Approve → resume → the tool runs and the run completes.
    resumed = await agent.approve(
        run_id="e2e", interrupt_id=paused.interrupt.interrupt_id
    )
    assert resumed.status.value == "completed"
    assert NOTES == ["hello from the embedded host"]
    assert hook.completed == ["e2e"]

    # 3) Durable abort — a pre-aborted handle stops a fresh run immediately.
    handle = RunAbortHandle()
    handle.abort("operator stop")
    cancelled = await agent.run(
        AgentRunInput(
            input="write another note",
            run_id="e2e_abort",
            agent_id="agent",
            graph_preset="single_react",
        ),
        abort_handle=handle,
    )
    assert cancelled.status.value == "cancelled"

    result = {
        "paused_interrupt": paused.interrupt.interrupt_id,
        "notes": list(NOTES),
        "completed_runs": list(hook.completed),
        "aborted_status": cancelled.status.value,
    }
    print(result)
    return result


if __name__ == "__main__":
    asyncio.run(main())
