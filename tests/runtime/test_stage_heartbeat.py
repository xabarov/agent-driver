"""Epic 025: stage-wait heartbeat — no silent long stage."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


class _SlowProvider(FakeProvider):
    def __init__(self, delay: float) -> None:
        super().__init__(response_text="ok")
        self._delay = delay

    async def complete(self, request: LlmRequest) -> LlmResponse:
        await asyncio.sleep(self._delay)
        return await super().complete(request)


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="question",
        run_id=run_id,
        thread_id="t-hb",
        agent_id="agent",
        graph_preset="single_react",
    )


@pytest.mark.asyncio
async def test_slow_llm_wait_emits_heartbeats() -> None:
    agent = create_agent(
        provider=_SlowProvider(0.35),
        tools=ToolSet.only(),
        config=RunnerConfig(stage_heartbeat_seconds=0.1),
    )
    output = await agent.run(_run_input("r-hb1"))
    beats = [
        e.payload
        for e in output.events
        if e.type.value == "warning"
        and e.payload.get("signal_id") == "stage_wait_heartbeat"
    ]
    assert len(beats) >= 2  # 0.35s wait with 0.1s interval → several beats
    assert beats[0]["stage"] == "llm_completion"
    assert beats[0]["severity"] == "info"
    assert beats[-1]["elapsed_ms"] >= 200
    assert output.answer == "ok"  # heartbeat never disturbs the run itself


@pytest.mark.asyncio
async def test_fast_stage_emits_no_heartbeat() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        config=RunnerConfig(stage_heartbeat_seconds=10.0),
    )
    output = await agent.run(_run_input("r-hb2"))
    beats = [
        e
        for e in output.events
        if e.type.value == "warning"
        and e.payload.get("signal_id") == "stage_wait_heartbeat"
    ]
    assert beats == []


@pytest.mark.asyncio
async def test_heartbeat_disabled_with_none() -> None:
    agent = create_agent(
        provider=_SlowProvider(0.25),
        tools=ToolSet.only(),
        config=RunnerConfig(stage_heartbeat_seconds=None),
    )
    output = await agent.run(_run_input("r-hb3"))
    beats = [
        e
        for e in output.events
        if e.type.value == "warning"
        and e.payload.get("signal_id") == "stage_wait_heartbeat"
    ]
    assert beats == []
