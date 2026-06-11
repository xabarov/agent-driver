"""N6: MemoryProvider.post_setup runs once; shutdown runs via Agent.aclose()."""

from __future__ import annotations

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import InMemoryMemoryStore, StoreBackedMemoryProvider
from agent_driver.memory.provider import RecallQuery, RecallResult
from agent_driver.sdk import ToolSet, create_agent


class _CountingMemoryProvider(StoreBackedMemoryProvider):
    """Records how often the lifecycle hooks fire."""

    def __init__(self) -> None:
        super().__init__(InMemoryMemoryStore())
        self.post_setups = 0
        self.shutdowns = 0

    async def post_setup(self) -> None:
        self.post_setups += 1

    async def shutdown(self) -> None:
        self.shutdowns += 1


@pytest.mark.asyncio
async def test_post_setup_runs_once_across_runs() -> None:
    """post_setup is invoked exactly once even across multiple runs/sessions."""
    memory = _CountingMemoryProvider()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=memory,
    )
    session = agent.session("u1")
    await session.send("first", run_id="r1")
    await session.send("second", run_id="r2")

    assert memory.post_setups == 1
    assert memory.shutdowns == 0  # not torn down yet


@pytest.mark.asyncio
async def test_aclose_calls_provider_shutdown() -> None:
    """Agent.aclose() (and async-with) flushes the memory provider once."""
    memory = _CountingMemoryProvider()
    async with create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=memory,
    ) as agent:
        await agent.session("u1").send("hi", run_id="r1")

    assert memory.post_setups == 1
    assert memory.shutdowns == 1


@pytest.mark.asyncio
async def test_aclose_isolates_failing_shutdown() -> None:
    """A shutdown that raises does not propagate out of aclose()."""

    class _BoomProvider(StoreBackedMemoryProvider):
        def __init__(self) -> None:
            super().__init__(InMemoryMemoryStore())

        async def prefetch(self, query: RecallQuery) -> RecallResult:
            return RecallResult(records=())

        async def shutdown(self) -> None:
            raise RuntimeError("boom: shutdown")

    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=_BoomProvider(),
    )
    await agent.aclose()  # must not raise
