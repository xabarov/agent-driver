"""End-to-end: long-term memory recalled across agent instances."""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse, LlmStreamEvent
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import (
    InMemoryMemoryStore,
    SqliteMemoryStore,
    StoreBackedMemoryProvider,
)
from agent_driver.sdk import ToolSet, create_agent


class _CapturingProvider(FakeProvider):
    """Fake provider that records the system prompt of each completion."""

    def __init__(self, *, response_text: str = "ok") -> None:
        super().__init__(name="capture", response_text=response_text)
        self.system_prompts: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.system_prompts.append(_system_text(request))
        return await super().complete(request)

    async def stream(self, request: LlmRequest) -> AsyncIterator[LlmStreamEvent]:
        self.system_prompts.append(_system_text(request))
        async for event in super().stream(request):
            yield event


def _system_text(request: LlmRequest) -> str:
    return "\n".join(
        message.content for message in request.messages if message.role == "system"
    )


@pytest.mark.asyncio
async def test_fact_recalled_in_later_run_same_session() -> None:
    """A fact stored on one run is injected into a later run's system prompt."""
    provider = _CapturingProvider()
    memory = StoreBackedMemoryProvider(InMemoryMemoryStore())
    agent = create_agent(
        provider=provider, tools=ToolSet.only(), memory_provider=memory
    )
    session = agent.session("user-1")

    await session.send("Remember: the deploy target is eu-west-3.", run_id="r1")
    provider.system_prompts.clear()
    await session.send("Where do we deploy?", run_id="r2")

    assert any("eu-west-3" in prompt for prompt in provider.system_prompts)


@pytest.mark.asyncio
async def test_memory_isolated_between_sessions() -> None:
    """One session's fact never leaks into another session's prompt."""
    provider = _CapturingProvider()
    memory = StoreBackedMemoryProvider(InMemoryMemoryStore())
    agent = create_agent(
        provider=provider, tools=ToolSet.only(), memory_provider=memory
    )

    await agent.session("user-1").send("Remember: the gate code is 4821.", run_id="r1")
    provider.system_prompts.clear()
    await agent.session("user-2").send("What is the gate code?", run_id="r2")

    assert all("4821" not in prompt for prompt in provider.system_prompts)


@pytest.mark.asyncio
async def test_durable_recall_across_agent_instances(tmp_path) -> None:
    """A fresh agent over the same SQLite store recalls an earlier fact.

    The second agent has an empty in-memory transcript, so recall here can
    only come from the durable long-term store — the cross-session value that
    session history alone cannot provide.
    """
    db_path = str(tmp_path / "memory.sqlite3")

    writer_store = SqliteMemoryStore(path=db_path)
    writer = create_agent(
        provider=_CapturingProvider(),
        tools=ToolSet.only(),
        memory_provider=StoreBackedMemoryProvider(writer_store),
    )
    await writer.session("user-7").send(
        "Remember: the API key is stored in Vault under prod/agent.", run_id="w1"
    )
    writer_store.close()

    reader_provider = _CapturingProvider()
    reader_store = SqliteMemoryStore(path=db_path)
    reader = create_agent(
        provider=reader_provider,
        tools=ToolSet.only(),
        memory_provider=StoreBackedMemoryProvider(reader_store),
    )
    await reader.session("user-7").send("Where is the API key?", run_id="r1")
    reader_store.close()

    assert any("Vault" in prompt for prompt in reader_provider.system_prompts)
