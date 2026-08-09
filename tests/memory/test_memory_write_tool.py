"""Memory epic M1 — model-callable `remember` tool + explicit-write flush.

Gives the agent agency over what to persist: the model calls `remember`, the
tool-stage buffers the write onto MemoryRuntimeState, and the MemoryLifecycleHook
flushes it to the durable store at run completion — SKIPPING the automatic
turn-sync/extraction for that turn (openclaude mutual exclusion).
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.contracts import LlmRequest, LlmResponse, LlmStreamEvent
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import InMemoryMemoryStore, StoreBackedMemoryProvider
from agent_driver.memory.provider import MemoryKind, sync_explicit_writes
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.tools.memory import _remember_tool, register_memory_tool
from agent_driver.tools.registry import ToolRegistry


# --- tool handler unit ---------------------------------------------------------


@pytest.mark.asyncio
async def test_remember_tool_returns_write_envelope() -> None:
    out = await _remember_tool({"content": "User prefers CSV output.", "slot": "fmt"})
    assert out["applied_memory_write"] == {
        "text": "User prefers CSV output.",
        "slot": "fmt",
    }
    assert "do not repeat" in out["next_action"].lower()


@pytest.mark.asyncio
async def test_remember_tool_requires_content() -> None:
    with pytest.raises(ValueError):
        await _remember_tool({"content": "   "})


@pytest.mark.asyncio
async def test_remember_tool_slot_optional() -> None:
    out = await _remember_tool({"content": "Deploy target is eu-west-3."})
    assert out["applied_memory_write"] == {
        "text": "Deploy target is eu-west-3.",
        "slot": None,
    }


# --- sync_explicit_writes unit -------------------------------------------------


def test_sync_explicit_writes_appends_fact_records() -> None:
    store = InMemoryMemoryStore()
    written = sync_explicit_writes(
        store,
        "s1",
        [{"text": "A", "slot": "k"}, {"text": "B", "slot": None}, {"text": " ", "slot": None}],
        run_id="run-1",
    )
    assert written == 2  # blank text skipped
    records = store.list_for_session("s1")
    assert {r.text for r in records} == {"A", "B"}
    for r in records:
        assert r.kind == MemoryKind.FACT
        assert r.metadata["source"] == "model_explicit"
        assert r.metadata["created_at"]
        assert r.metadata["run_id"] == "run-1"
    slotted = next(r for r in records if r.text == "A")
    assert slotted.metadata["slot"] == "k"


# --- registration gating -------------------------------------------------------


def test_remember_registered_only_with_memory_provider() -> None:
    from agent_driver.runtime.single_agent.types import RunnerConfig
    from agent_driver.sdk.factory import build_default_registry

    plain = build_default_registry(RunnerConfig())
    assert plain.get("remember") is None

    with_mem = build_default_registry(
        RunnerConfig(memory_provider=StoreBackedMemoryProvider(InMemoryMemoryStore()))
    )
    assert with_mem.get("remember") is not None


def test_register_memory_tool_idempotent() -> None:
    registry = ToolRegistry()
    register_memory_tool(registry)
    register_memory_tool(registry)
    assert registry.get("remember") is not None


# --- integration: agency + mutual exclusion + recall ---------------------------


class _CapturingProvider(FakeProvider):
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
    return "\n".join(m.content for m in request.messages if m.role == "system")


def _remember_call(content: str, slot: str | None = None) -> dict:
    args: dict = {"content": content}
    if slot is not None:
        args["slot"] = slot
    return {
        "metadata": {
            "planned_tool_calls": [
                ToolCall(
                    tool_name="remember", tool_call_id="c1", args=args
                ).model_dump(mode="json")
            ]
        }
    }


@pytest.mark.asyncio
async def test_remember_persists_explicit_fact_and_skips_auto_sync() -> None:
    store = InMemoryMemoryStore()
    agent = create_agent(
        provider=_CapturingProvider(),
        tools=ToolSet.only("remember"),
        memory_provider=StoreBackedMemoryProvider(store),
    )
    await agent.run(
        AgentRunInput(
            input="Please always give me CSV.",
            run_id="r1",
            agent_id="agent",
            thread_id="user-1",
            graph_preset="single_react",
            tool_policy=_remember_call("The user wants CSV output.", slot="fmt"),
        )
    )
    records = store.list_for_session("user-1")
    # Only the explicit FACT is stored; the raw user/assistant turn is NOT
    # (mutual exclusion — the model curated memory itself this turn).
    assert len(records) == 1
    assert records[0].kind == MemoryKind.FACT
    assert records[0].text == "The user wants CSV output."
    assert records[0].metadata["source"] == "model_explicit"
    assert all(r.metadata.get("role") not in {"user", "assistant"} for r in records)


@pytest.mark.asyncio
async def test_explicit_writes_route_through_a_rescoping_provider() -> None:
    """Epic M6 seam: the hook records explicit writes via the provider method, so
    a provider that rewrites the session id persists facts under ITS scope — not
    the raw run thread_id. Without this, a workbook-scoped wrapper would store
    `remember` facts under the conversation and never recall them."""
    store = InMemoryMemoryStore()

    class _RescopingProvider(StoreBackedMemoryProvider):
        async def record_explicit_writes(self, session_id, writes, *, run_id=None):
            return await super().record_explicit_writes(
                "workbook-scope", writes, run_id=run_id
            )

    agent = create_agent(
        provider=_CapturingProvider(),
        tools=ToolSet.only("remember"),
        memory_provider=_RescopingProvider(store),
    )
    await agent.run(
        AgentRunInput(
            input="note it",
            run_id="r1",
            agent_id="agent",
            thread_id="conversation-42",
            graph_preset="single_react",
            tool_policy=_remember_call("Column F is net revenue in EUR.", slot="col-f"),
        )
    )
    # Stored under the provider's scope, NOT the run thread_id.
    assert store.list_for_session("workbook-scope")
    assert not store.list_for_session("conversation-42")


@pytest.mark.asyncio
async def test_explicit_fact_recalled_in_later_run() -> None:
    provider = _CapturingProvider()
    store = InMemoryMemoryStore()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("remember"),
        memory_provider=StoreBackedMemoryProvider(store),
    )
    session_id = "user-2"
    await agent.run(
        AgentRunInput(
            input="Note my preference.",
            run_id="r1",
            agent_id="agent",
            thread_id=session_id,
            graph_preset="single_react",
            tool_policy=_remember_call("Deploy target is eu-west-3.", slot="deploy"),
        )
    )
    provider.system_prompts.clear()
    await agent.run(
        AgentRunInput(
            input="Where do we deploy?",
            run_id="r2",
            agent_id="agent",
            thread_id=session_id,
            graph_preset="single_react",
        )
    )
    assert any("eu-west-3" in p for p in provider.system_prompts)


@pytest.mark.asyncio
async def test_reused_slot_supersedes_on_recall() -> None:
    provider = _CapturingProvider()
    store = InMemoryMemoryStore()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("remember"),
        memory_provider=StoreBackedMemoryProvider(store),
    )
    sid = "user-3"
    for run_id, target in (("r1", "eu-west-3"), ("r2", "us-east-1")):
        await agent.run(
            AgentRunInput(
                input="update",
                run_id=run_id,
                agent_id="agent",
                thread_id=sid,
                graph_preset="single_react",
                tool_policy=_remember_call(
                    f"Deploy target is {target}.", slot="deploy"
                ),
            )
        )
    provider.system_prompts.clear()
    await agent.run(
        AgentRunInput(
            input="Where do we deploy?",
            run_id="r3",
            agent_id="agent",
            thread_id=sid,
            graph_preset="single_react",
        )
    )
    recalled = "\n".join(provider.system_prompts)
    # Both facts share a slot → recall keeps only the newest (supersede_by_slot).
    assert "us-east-1" in recalled
    assert "eu-west-3" not in recalled
