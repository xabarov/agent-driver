"""Epic 031: the memory hook fires consolidation on the host-supplied cadence."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import InMemoryMemoryStore, StoreBackedMemoryProvider
from agent_driver.memory.provider import ConsolidationResult
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


class _ConsolidatingProvider(StoreBackedMemoryProvider):
    """Deferred provider that records every consolidation trigger."""

    defer_sync = True

    def __init__(self) -> None:
        super().__init__(InMemoryMemoryStore())
        self.consolidate_calls: list[str] = []

    async def sync_turn(
        self, turn
    ) -> None:  # noqa: ANN001 - instant, off critical path
        return None

    async def consolidate(self, session_id, *, cost_ledger=None):  # noqa: ANN001
        self.consolidate_calls.append(session_id)
        return ConsolidationResult(
            session_id=session_id,
            before_count=3,
            after_count=2,
            merged=1,
            applied=True,
            reason="applied",
        )


def _run_input(ordinal: int, *, run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hi",
        run_id=run_id,
        thread_id="user-1",
        agent_id="agent",
        graph_preset="single_react",
        app_metadata={"memory": {"turn_ordinal": ordinal}},
    )


async def _run_once(memory, every_n: int, ordinal: int, *, run_id="r1") -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=memory,
        config=RunnerConfig(memory_consolidation_every_n_turns=every_n),
    )
    await agent.run(_run_input(ordinal, run_id=run_id))
    await agent.aclose()  # drain the background sync+consolidate task


@pytest.mark.asyncio
async def test_consolidation_fires_on_cadence_turn() -> None:
    memory = _ConsolidatingProvider()
    await _run_once(memory, every_n=2, ordinal=2)
    assert memory.consolidate_calls == ["user-1"]


@pytest.mark.asyncio
async def test_consolidation_skips_non_cadence_turn() -> None:
    memory = _ConsolidatingProvider()
    await _run_once(memory, every_n=2, ordinal=1)
    assert memory.consolidate_calls == []


@pytest.mark.asyncio
async def test_consolidation_disabled_when_interval_zero() -> None:
    memory = _ConsolidatingProvider()
    await _run_once(memory, every_n=0, ordinal=4)
    assert memory.consolidate_calls == []


@pytest.mark.asyncio
async def test_missing_ordinal_never_fires() -> None:
    memory = _ConsolidatingProvider()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        memory_provider=memory,
        config=RunnerConfig(memory_consolidation_every_n_turns=2),
    )
    # No app_metadata memory.turn_ordinal at all → ordinal defaults to 0 → no fire.
    await agent.run(
        AgentRunInput(
            input="hi",
            run_id="r1",
            thread_id="user-1",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    await agent.aclose()
    assert memory.consolidate_calls == []


@pytest.mark.asyncio
async def test_provider_without_consolidate_is_inert() -> None:
    # Base StoreBackedMemoryProvider.consolidate returns None → hook must not crash.
    class _PlainDeferred(StoreBackedMemoryProvider):
        defer_sync = True

        def __init__(self) -> None:
            super().__init__(InMemoryMemoryStore())

        async def sync_turn(self, turn) -> None:  # noqa: ANN001
            return None

    memory = _PlainDeferred()
    await _run_once(
        memory, every_n=1, ordinal=1
    )  # cadence lands, but no-op consolidate
