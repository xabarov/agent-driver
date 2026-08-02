"""U4 (epic 052) — cooperative host cancellation hook into a running handler.

Covers the additive slice:
- when the runtime plumbs an abort handle into the executor, a handler sees a
  ToolCancellation via current_tool_cancellation() with run/call/attempt
  identity;
- with no abort handle (default), the token is None → handlers run as before;
- a handler awaiting wait_cancelled() returns promptly when the run is aborted
  mid-flight (the cooperative cancel signal actually fires);
- once an abort is already observed, a not-yet-started call is skipped (no new
  work / no tool side-effect) and a `run_aborted` block is recorded.

Locks the contract at the governed-executor layer (mirrors test_tool_gate.py).
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.cancellation import ToolCancellation
from agent_driver.tools.context import current_tool_cancellation
from tests.runtime.conftest import llm_request_with_planned_calls


def _read_manifest(name: str = "lookup") -> ToolManifest:
    return ToolManifest(
        name=name,
        description="Read-only lookup tool",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
    )


def _run_input() -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id="run_cancel_test",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


async def _execute(executor, planned, *, abort_handle=None):
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[planned])
    )
    return await executor.execute(
        _run_input(), response, abort_handle=abort_handle
    )


@pytest.mark.asyncio
async def test_cancellation_token_carries_bounded_deadline() -> None:
    registry = ToolRegistry()
    seen: list[ToolCancellation | None] = []

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(current_tool_cancellation())
        return {"ok": True}

    registry.register(_read_manifest(), _handler)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="hi",
        run_id="run_deadline",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
        deadline_seconds=30.0,
    )
    provider = FakeProvider(response_text="ok")
    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={})
    response = await provider.complete(llm_request_with_planned_calls(planned=[call]))
    await executor.execute(run_input, response, abort_handle=RunAbortHandle())
    assert seen and seen[0] is not None
    assert seen[0].deadline_seconds == 30.0


@pytest.mark.asyncio
async def test_handler_sees_cancellation_token_with_identity() -> None:
    registry = ToolRegistry()
    seen: list[ToolCancellation | None] = []

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(current_tool_cancellation())
        return {"ok": True}

    registry.register(_read_manifest(), _handler)
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="lookup", tool_call_id="tc-9", args={"q": "x"})
    await _execute(executor, call, abort_handle=RunAbortHandle())
    assert seen and seen[0] is not None
    token = seen[0]
    assert token.tool_call_id == "tc-9"
    assert token.run_id == "run_cancel_test"
    assert token.attempt_id
    assert token.is_cancelled is False


@pytest.mark.asyncio
async def test_no_abort_handle_means_no_token() -> None:
    registry = ToolRegistry()
    seen: list[ToolCancellation | None] = []

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        seen.append(current_tool_cancellation())
        return {"ok": True}

    registry.register(_read_manifest(), _handler)
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    await _execute(executor, call)  # no abort handle
    assert seen == [None]


@pytest.mark.asyncio
async def test_handler_wait_cancelled_returns_on_mid_flight_abort() -> None:
    registry = ToolRegistry()
    started = asyncio.Event()

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        token = current_tool_cancellation()
        assert token is not None
        started.set()
        await token.wait_cancelled(poll_interval_s=0.01)
        return {"stopped": True}

    registry.register(_read_manifest(), _handler)
    executor = GovernedToolExecutor(registry=registry)
    handle = RunAbortHandle()
    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})

    async def _abort_when_started() -> None:
        await started.wait()
        handle.abort("operator stop")

    exec_task = asyncio.create_task(_execute(executor, call, abort_handle=handle))
    await asyncio.gather(_abort_when_started(), exec_task)
    result = exec_task.result()
    # The handler observed cancellation and returned; a completed envelope exists.
    assert len(result.envelopes) == 1


@pytest.mark.asyncio
async def test_already_aborted_skips_new_work() -> None:
    registry = ToolRegistry()
    calls: list[dict[str, Any]] = []

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        calls.append(dict(args))
        return {"ok": True}

    registry.register(_read_manifest(), _handler)
    executor = GovernedToolExecutor(registry=registry)
    handle = RunAbortHandle()
    handle.abort("stop before start")
    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, call, abort_handle=handle)
    # Handler never ran (no new work once observed); a block was recorded.
    assert calls == []
    assert any(
        (env.error and env.error.code == "run_aborted") for env in result.envelopes
    )
