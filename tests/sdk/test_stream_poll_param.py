"""DX: typed stream_poll_interval_ms supersedes the app_metadata magic key."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _agent():
    return create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())


def _input(run_id: str, **kw) -> AgentRunInput:
    return AgentRunInput(
        input="q", run_id=run_id, agent_id="a", graph_preset="single_react", **kw
    )


async def _interval(agent, run_input, **kw) -> float:
    stream = agent.stream_run(run_input, **kw)
    interval = stream._poll_interval_seconds
    await stream.final_output()  # let the underlying run task complete cleanly
    return interval


@pytest.mark.asyncio
async def test_typed_param_sets_poll_interval() -> None:
    assert await _interval(_agent(), _input("r1"), stream_poll_interval_ms=250) == 0.25


@pytest.mark.asyncio
async def test_app_metadata_fallback_still_honored() -> None:
    assert await _interval(
        _agent(), _input("r2", app_metadata={"stream_poll_interval_ms": 100})
    ) == 0.1


@pytest.mark.asyncio
async def test_typed_param_wins_over_app_metadata() -> None:
    assert await _interval(
        _agent(),
        _input("r3", app_metadata={"stream_poll_interval_ms": 100}),
        stream_poll_interval_ms=500,
    ) == 0.5


@pytest.mark.asyncio
async def test_default_is_20ms() -> None:
    assert await _interval(_agent(), _input("r4")) == 0.02
