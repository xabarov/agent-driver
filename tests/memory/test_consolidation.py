"""Epic 031: background memory consolidation over the fact store."""

from __future__ import annotations

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import (
    FactExtractingMemoryProvider,
    InMemoryMemoryStore,
    MemoryKind,
    MemoryRecord,
)
from agent_driver.memory.consolidation import consolidate_session
from agent_driver.memory.stores import SqliteMemoryStore


class _ConsolidationProvider(FakeProvider):
    """Fake provider emitting a queued consolidation ``{facts:[...]}`` per call."""

    def __init__(self, fact_sets: list[list[dict]]) -> None:
        super().__init__(name="consolidator", response_text="{}")
        self._fact_sets = list(fact_sets)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        response = await super().complete(request)
        facts = self._fact_sets.pop(0) if self._fact_sets else []
        meta = dict(response.message.metadata or {})
        meta["planned_tool_calls"] = [{"name": "emit_result", "args": {"facts": facts}}]
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


class _ExplodingProvider(FakeProvider):
    async def complete(self, request: LlmRequest) -> LlmResponse:
        raise RuntimeError("consolidation backend down")


class _AppendOnlyStore:
    """A store WITHOUT replace_session — consolidation must stay inert on it."""

    def __init__(self, records: list[MemoryRecord]) -> None:
        self._records = list(records)

    def append(self, record: MemoryRecord) -> MemoryRecord:  # pragma: no cover
        self._records.append(record)
        return record

    def list_for_session(self, session_id, *, limit=None):
        out = list(reversed(self._records))
        return out[:limit] if limit is not None else out

    def clear(self, session_id) -> None:  # pragma: no cover
        self._records = []


def _fact(
    text: str, slot: str | None = None, *, seq: int = 0, source: str = "fact_extraction"
) -> MemoryRecord:
    metadata: dict = {"source": source}
    if slot:
        metadata["slot"] = slot
    return MemoryRecord(
        session_id="u1", text=text, kind=MemoryKind.FACT, metadata=metadata, seq=seq
    )


def _seed(store, records: list[MemoryRecord]) -> None:
    for record in records:
        store.append(record.model_copy(update={"seq": 0}))


@pytest.mark.asyncio
async def test_llm_merges_cross_slot_and_absolutizes_date() -> None:
    # Distinct slots (deterministic supersede leaves them alone) so the LLM path
    # is what merges a cross-slot semantic duplicate and absolutizes the date.
    store = InMemoryMemoryStore()
    _seed(
        store,
        [
            _fact("Пользователь любит краткие ответы.", "answer-format"),
            _fact("Отвечай пользователю кратко.", "answer-style"),
            _fact("Кодовое слово команды — Оникс.", "team-codeword"),
            _fact("Работает над проектом «Аргус».", "current-project"),
            _fact("Установочная встреча была вчера.", "last-meeting"),
        ],
    )
    provider = _ConsolidationProvider(
        [
            [
                {
                    "text": "Пользователь предпочитает краткие ответы.",
                    "slot": "answer-format",
                },
                {"text": "Кодовое слово команды — Оникс.", "slot": "team-codeword"},
                {"text": "Работает над проектом «Аргус».", "slot": "current-project"},
                {
                    "text": "Установочная встреча была 2026-07-23.",
                    "slot": "last-meeting",
                },
            ]
        ]
    )
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is True
    assert result.reason == "applied"
    assert result.before_count == 5
    assert result.after_count == 4
    assert result.merged == 1
    assert len(provider.requests) == 1
    stored = {r.text for r in store.list_for_session("u1")}
    assert "Установочная встреча была 2026-07-23." in stored
    assert not any("вчера" in text for text in stored)  # relative date absolutized
    assert all(
        r.metadata.get("source") == "consolidated" for r in store.list_for_session("u1")
    )


@pytest.mark.asyncio
async def test_deterministic_collapse_without_llm() -> None:
    # Same-slot dups + a contradiction pair collapse deterministically; the result
    # is below the LLM gate, so it is persisted WITHOUT spending a call.
    store = InMemoryMemoryStore()
    _seed(
        store,
        [
            _fact("Кодовое слово — Оникс.", "team-codeword"),
            _fact("Теперь кодовое слово — Сапфир.", "team-codeword"),
            _fact("Работает над «Аргус».", "current-project"),
        ],
    )
    provider = _ConsolidationProvider([[{"text": "должно быть проигнорировано"}]])
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is True
    assert result.reason == "deterministic"
    assert result.before_count == 3
    assert result.after_count == 2
    assert provider.requests == []  # deterministic path spends no LLM call
    stored = {r.text for r in store.list_for_session("u1")}
    assert "Теперь кодовое слово — Сапфир." in stored
    assert "Кодовое слово — Оникс." not in stored


@pytest.mark.asyncio
async def test_below_gate_skips_llm_and_store() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact("Любит Python.", "language"), _fact("Живёт в Москве.", "city")])
    provider = _ConsolidationProvider([[{"text": "должно быть проигнорировано"}]])
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is False
    assert result.reason == "below_gate"
    assert provider.requests == []  # cheap gate: no LLM spend
    assert len(store.list_for_session("u1")) == 2


@pytest.mark.asyncio
async def test_relative_date_forces_pass_below_count_gate() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact("Встреча по проекту была вчера.", "last-meeting")])
    provider = _ConsolidationProvider(
        [[{"text": "Встреча по проекту была 2026-07-23.", "slot": "last-meeting"}]]
    )
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is True
    assert len(provider.requests) == 1  # relative-date marker justified the call


@pytest.mark.asyncio
async def test_empty_result_is_guarded_not_a_wipe() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact(f"Факт {i}.", f"slot-{i}") for i in range(4)])
    provider = _ConsolidationProvider([[]])  # model returns no facts
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is False
    assert result.reason == "empty_result_guarded"
    assert len(store.list_for_session("u1")) == 4  # store untouched


@pytest.mark.asyncio
async def test_would_grow_is_guarded() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact(f"Факт {i}.", f"slot-{i}") for i in range(4)])
    provider = _ConsolidationProvider(
        [[{"text": f"Новый факт {i}."} for i in range(6)]]  # grows 4 -> 6
    )
    memory = FactExtractingMemoryProvider(store, provider)

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is False
    assert result.reason == "would_grow_guarded"
    assert len(store.list_for_session("u1")) == 4


@pytest.mark.asyncio
async def test_aux_failure_leaves_store_untouched() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact(f"Факт {i}.", f"slot-{i}") for i in range(4)])
    memory = FactExtractingMemoryProvider(store, _ExplodingProvider())

    result = await memory.consolidate("u1")

    assert result is not None
    assert result.applied is False
    assert result.reason == "aux_call_failed"
    assert len(store.list_for_session("u1")) == 4


@pytest.mark.asyncio
async def test_store_without_replace_session_is_inert() -> None:
    records = [_fact(f"Факт {i}.", f"slot-{i}", seq=i + 1) for i in range(4)]
    store = _AppendOnlyStore(records)
    provider = _ConsolidationProvider([[{"text": "не должно примениться"}]])

    result = await consolidate_session(
        store=store, llm_provider=provider, session_id="u1"
    )

    assert result.applied is False
    assert result.reason == "store_cannot_rewrite"
    assert provider.requests == []  # gated before any spend


@pytest.mark.asyncio
async def test_replace_session_roundtrip_in_memory() -> None:
    store = InMemoryMemoryStore()
    _seed(store, [_fact("old A", "a"), _fact("old B", "b")])
    new = [_fact("kept", "k")]

    persisted = store.replace_session("u1", new)

    assert [r.text for r in persisted] == ["kept"]
    listed = store.list_for_session("u1")
    assert [r.text for r in listed] == ["kept"]
    assert listed[0].seq > 0


@pytest.mark.asyncio
async def test_replace_session_roundtrip_sqlite(tmp_path) -> None:
    store = SqliteMemoryStore(path=str(tmp_path / "mem.db"))
    _seed(store, [_fact("old A", "a"), _fact("old B", "b"), _fact("old C", "c")])
    new = [_fact("one", "x"), _fact("two", "y")]

    store.replace_session("u1", new)

    listed = store.list_for_session("u1")
    # Input is newest-first ([one, two]); output preserves that order.
    assert [r.text for r in listed] == ["one", "two"]
    assert listed[0].seq > listed[1].seq
