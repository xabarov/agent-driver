"""Deterministic runtime proof for soft steer, redirect, and generation fencing."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ChatMessage,
    CommandQueueStatus,
    LiveMessagePhase,
    LiveMessageSemantic,
    RuntimeEventType,
    ToolCall,
)
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import InMemoryCommandQueueStore
from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmGenerationSuperseded,
)
from agent_driver.sdk import ToolSet, create_agent


def _input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="initial",
        run_id=run_id,
        thread_id=f"thread-{run_id}",
        agent_id="agent",
        graph_preset="single_react",
    )


def _response(
    content: str,
    *,
    finish_reason: LlmFinishReason = LlmFinishReason.STOP,
    tool_name: str | None = None,
) -> LlmResponse:
    metadata = {}
    if tool_name:
        metadata["planned_tool_calls"] = [
            ToolCall(
                tool_name=tool_name,
                tool_call_id="synthetic-boundary-call",
                args={},
            ).model_dump(mode="json")
        ]
    return LlmResponse(
        message=ChatMessage(role="assistant", content=content),
        finish_reason=finish_reason,
        usage=UsageSummary(model_provider="fake", model_name="live-message-test"),
        provider="fake",
        model="live-message-test",
        metadata=metadata,
    )


class _BlockedFirstLlmProvider(FakeProvider):
    def __init__(self, *, first_tool: str | None = None) -> None:
        super().__init__(response_text="unused")
        self.first_tool = first_tool
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.cancelled = False
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
            if self.first_tool:
                return _response(
                    "",
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    tool_name=self.first_tool,
                )
        return _response("done")


@pytest.mark.asyncio
async def test_soft_steer_does_not_abort_blocked_llm_and_applies_at_boundary() -> None:
    store = InMemoryCommandQueueStore()
    provider = _BlockedFirstLlmProvider(first_tool="synthetic_boundary")
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=store,
    )
    tool_calls = 0

    async def synthetic_boundary() -> dict[str, object]:
        nonlocal tool_calls
        tool_calls += 1
        return {"synthetic": True}

    agent.add_tool(synthetic_boundary)
    run_task = asyncio.create_task(agent.run(_input("run-soft")))
    await asyncio.wait_for(provider.started.wait(), timeout=2)

    accepted = agent.steer("soft correction", run_id="run-soft")
    assert accepted.ok is True
    assert provider.cancelled is False
    assert run_task.done() is False

    provider.release.set()
    output = await asyncio.wait_for(run_task, timeout=5)
    receipt = store.get(accepted.queue_id or "")

    assert provider.cancelled is False
    assert len(provider.requests) == 2
    assert tool_calls == 1
    assert any(
        message.content == "soft correction"
        for message in provider.requests[1].messages
    )
    assert receipt is not None
    assert receipt.status is CommandQueueStatus.APPLIED
    assert receipt.requested_semantic is LiveMessageSemantic.STEER_CURRENT
    assert receipt.resolved_semantic is LiveMessageSemantic.STEER_CURRENT
    assert any(
        event.type is RuntimeEventType.COMMAND_APPLIED for event in output.events
    )


@pytest.mark.asyncio
async def test_durable_hard_redirect_cancels_only_llm_and_reasks() -> None:
    store = InMemoryCommandQueueStore()
    provider = _BlockedFirstLlmProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=store,
    )
    run_task = asyncio.create_task(agent.run(_input("run-redirect")))
    await asyncio.wait_for(provider.started.wait(), timeout=2)

    accepted = agent.redirect("urgent correction", run_id="run-redirect")
    output = await asyncio.wait_for(run_task, timeout=5)
    receipt = store.get(accepted.queue_id or "")

    assert provider.cancelled is True
    assert len(provider.requests) == 2
    assert any(
        message.content == "urgent correction"
        for message in provider.requests[1].messages
    )
    assert receipt is not None
    assert receipt.status is CommandQueueStatus.APPLIED
    assert receipt.resolved_semantic is LiveMessageSemantic.REDIRECT_CURRENT
    redirect_events = [
        event
        for event in output.events
        if event.type is RuntimeEventType.COMMAND_REDIRECTED
    ]
    assert len(redirect_events) == 1
    assert redirect_events[0].payload["content_sha256"] == receipt.content_sha256
    assert "urgent correction" not in str(redirect_events[0].payload)


class _ToolThenFinalProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if len(self.requests) == 1:
            return _response(
                "",
                finish_reason=LlmFinishReason.TOOL_CALLS,
                tool_name="blocked_synthetic_tool",
            )
        return _response("done")


@pytest.mark.asyncio
async def test_redirect_during_tool_degrades_without_cancelling_tool() -> None:
    store = InMemoryCommandQueueStore()
    provider = _ToolThenFinalProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=store,
    )
    tool_started = asyncio.Event()
    release_tool = asyncio.Event()
    tool_cancelled = False

    async def blocked_synthetic_tool() -> dict[str, object]:
        nonlocal tool_cancelled
        tool_started.set()
        try:
            await release_tool.wait()
        except asyncio.CancelledError:
            tool_cancelled = True
            raise
        return {"synthetic": True}

    agent.add_tool(blocked_synthetic_tool)
    run_task = asyncio.create_task(agent.run(_input("run-tool-redirect")))
    await asyncio.wait_for(tool_started.wait(), timeout=2)

    accepted = agent.redirect("degrade me", run_id="run-tool-redirect")
    assert tool_cancelled is False
    release_tool.set()
    output = await asyncio.wait_for(run_task, timeout=5)
    receipt = store.get(accepted.queue_id or "")

    assert tool_cancelled is False
    assert receipt is not None
    assert receipt.status is CommandQueueStatus.APPLIED
    assert receipt.requested_semantic is LiveMessageSemantic.REDIRECT_CURRENT
    assert receipt.resolved_semantic is LiveMessageSemantic.STEER_CURRENT
    assert receipt.reason_code == "redirect_degraded_tool_phase"
    assert any(
        message.content == "degrade me" for message in provider.requests[1].messages
    )
    assert any(
        event.type is RuntimeEventType.COMMAND_APPLIED
        and event.payload.get("reason_code") == "redirect_degraded_tool_phase"
        for event in output.events
    )


class _UncooperativeProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.started = asyncio.Event()
        self.release_late = asyncio.Event()
        self.cancel_observed = asyncio.Event()

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.started.set()
        try:
            await self.release_late.wait()
        except asyncio.CancelledError:
            self.cancel_observed.set()
            await self.release_late.wait()
        return _response("stale answer")


@pytest.mark.asyncio
async def test_other_worker_generation_advance_fences_late_completion() -> None:
    store = InMemoryCommandQueueStore()
    provider = _UncooperativeProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        command_queue_store=store,
    )
    run_task = asyncio.create_task(agent.run(_input("run-generation")))
    await asyncio.wait_for(provider.started.wait(), timeout=2)
    accepted = agent.redirect("owned by new worker", run_id="run-generation")
    claimed = store.claim_hard_redirect(
        run_id="run-generation",
        claimant_id="replacement-worker",
        expected_generation=0,
    )
    assert claimed is not None
    assert claimed.queue_id == accepted.queue_id

    with pytest.raises(LlmGenerationSuperseded):
        await asyncio.wait_for(run_task, timeout=3)
    await asyncio.wait_for(provider.cancel_observed.wait(), timeout=2)
    provider.release_late.set()
    await asyncio.sleep(0)

    event_types = {
        event.type
        for event in agent.runner.deps.event_log.list_for_run("run-generation")
    }
    assert RuntimeEventType.RESULT_FENCED in event_types
    assert RuntimeEventType.ASSISTANT_MESSAGE_COMPLETED not in event_types
    assert RuntimeEventType.RUN_COMPLETED not in event_types
    assert RuntimeEventType.RUN_FAILED not in event_types
    assert store.get_run_state("run-generation").phase is LiveMessagePhase.LLM_IN_FLIGHT
