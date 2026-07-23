"""Epic 021 phase B: fact extraction + append-only slot supersede."""

from __future__ import annotations

import json

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import (
    FactExtractingMemoryProvider,
    InMemoryMemoryStore,
    MemoryKind,
    MemoryTurn,
    RecallQuery,
    parse_extracted_facts,
    supersede_by_slot,
)


class _ScriptedProvider(FakeProvider):
    """Fake provider returning queued responses (one per complete() call)."""

    def __init__(self, responses: list[str]) -> None:
        super().__init__(name="scripted", response_text="[]")
        self._responses = list(responses)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        text = self._responses.pop(0) if self._responses else "[]"
        response = await super().complete(request)
        # Epic 036: extraction now uses the forced tool-call channel. Translate a
        # queued JSON-array reply into an ``emit_result`` tool call ({facts:[...]});
        # an unparseable prose reply emits NO tool call (simulates the flake).
        meta = dict(response.message.metadata or {})
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                meta["planned_tool_calls"] = [
                    {"name": "emit_result", "args": {"facts": parsed}}
                ]
        except (json.JSONDecodeError, TypeError):
            pass  # prose → no tool call
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


class _ExplodingProvider(FakeProvider):
    async def complete(self, request: LlmRequest) -> LlmResponse:
        raise RuntimeError("extraction backend down")


def _turn(user: str, assistant: str = "ok") -> MemoryTurn:
    return MemoryTurn(
        session_id="u1", run_id="r1", user_text=user, assistant_text=assistant
    )


@pytest.mark.asyncio
async def test_facts_stored_with_slots_and_recalled() -> None:
    provider = _ScriptedProvider(
        [
            json.dumps(
                [{"text": "Кодовое слово команды — селенит.", "slot": "team-codeword"}]
            )
        ]
    )
    memory = FactExtractingMemoryProvider(InMemoryMemoryStore(), provider)
    await memory.sync_turn(_turn("Запомни: кодовое слово — селенит."))

    result = await memory.prefetch(RecallQuery(session_id="u1", query="кодовое слово"))
    assert [r.text for r in result.records] == ["Кодовое слово команды — селенит."]
    assert result.records[0].kind is MemoryKind.FACT
    assert result.records[0].metadata["slot"] == "team-codeword"
    # The extraction request must carry the turn text, not be empty.
    assert "селенит" in provider.requests[0].messages[-1].content


@pytest.mark.asyncio
async def test_same_slot_fact_supersedes_older_one() -> None:
    provider = _ScriptedProvider(
        [
            json.dumps([{"text": "Кодовое слово — селенит.", "slot": "team-codeword"}]),
            json.dumps([{"text": "Кодовое слово — азурит.", "slot": "team-codeword"}]),
        ]
    )
    memory = FactExtractingMemoryProvider(InMemoryMemoryStore(), provider)
    await memory.sync_turn(_turn("кодовое слово — селенит"))
    await memory.sync_turn(_turn("теперь кодовое слово — азурит"))

    result = await memory.prefetch(RecallQuery(session_id="u1", query="кодовое слово"))
    texts = [r.text for r in result.records]
    assert texts == ["Кодовое слово — азурит."]


@pytest.mark.asyncio
async def test_unparseable_extraction_falls_back_to_raw_turn() -> None:
    # Epic 027: одна непарсибельная реплика теперь чинится bounded-ретраем;
    # fallback на raw-turn — только после ДВУХ подряд (см. test_recall_hygiene).
    provider = _ScriptedProvider(
        ["I could not produce JSON, sorry.", "still not JSON, sorry"]
    )
    store = InMemoryMemoryStore()
    memory = FactExtractingMemoryProvider(store, provider)
    await memory.sync_turn(_turn("важный вопрос", "важный ответ"))

    stored = store.list_for_session("u1")
    assert {r.text for r in stored} == {"важный вопрос", "важный ответ"}
    assert all(r.kind is MemoryKind.TURN for r in stored)
    assert all(r.metadata.get("source") == "raw_fallback" for r in stored)


@pytest.mark.asyncio
async def test_provider_error_fails_open_without_raising() -> None:
    store = InMemoryMemoryStore()
    memory = FactExtractingMemoryProvider(
        store, _ExplodingProvider(), fallback_raw=False
    )
    await memory.sync_turn(_turn("вопрос"))  # must not raise
    assert store.list_for_session("u1") == []


@pytest.mark.asyncio
async def test_empty_extraction_stores_nothing() -> None:
    store = InMemoryMemoryStore()
    memory = FactExtractingMemoryProvider(store, _ScriptedProvider(["[]"]))
    await memory.sync_turn(_turn("сколько будет 2+2?", "4"))
    assert store.list_for_session("u1") == []


def test_parse_extracted_facts_tolerates_fences_and_prose() -> None:
    content = 'Вот факты:\n```json\n[{"text": "A", "slot": "S"}, {"bad": 1}]\n```'
    assert parse_extracted_facts(content, max_facts=5) == [{"text": "A", "slot": "s"}]
    with pytest.raises(ValueError):
        parse_extracted_facts("no json here", max_facts=5)


def test_supersede_keeps_slotless_records() -> None:
    from agent_driver.memory import MemoryRecord

    records = [
        MemoryRecord(session_id="s", text="new", metadata={"slot": "k"}, seq=3),
        MemoryRecord(session_id="s", text="raw turn", seq=2),
        MemoryRecord(session_id="s", text="old", metadata={"slot": "k"}, seq=1),
    ]
    assert [r.text for r in supersede_by_slot(records)] == ["new", "raw turn"]


def test_parse_drops_placeholder_slot_and_duplicate_texts() -> None:
    content = json.dumps(
        [
            {"text": "A", "slot": "short-stable-kebab-key-naming-the-subject"},
            {"text": "A", "slot": "real-key"},
            {"text": "B", "slot": "x" * 41},
        ]
    )
    facts = parse_extracted_facts(content, max_facts=5)
    assert facts == [{"text": "A"}, {"text": "B"}]
