"""Epic 027: recall hygiene — abstain gate, decay, raw-fallback rank, sanitizer."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.memory import InMemoryMemoryStore
from agent_driver.memory.extraction import FactExtractingMemoryProvider
from agent_driver.memory.provider import (
    MemoryKind,
    MemoryRecord,
    MemoryTurn,
    RecallQuery,
    apply_recall,
    render_recall_block,
    sanitize_memory_text,
    score_relevance,
)


def _rec(text: str, *, meta: dict | None = None) -> MemoryRecord:
    return MemoryRecord(session_id="s", text=text, metadata=meta or {})


def test_abstain_gate_drops_unrelated_memories() -> None:
    """An unrelated question recalls NOTHING instead of the closest garbage."""
    records = [
        _rec("Пользователь предпочитает сводки таблицей"),
        _rec("Бюджет проекта Аргус увеличен до 14,5 млн"),
    ]
    # Short particles («по», «до») no longer match everything.
    out = apply_recall(records, "когда была та встреча по маркетингу?", 5)
    assert out == []


def test_relevance_scoring_and_threshold() -> None:
    assert score_relevance(
        "Бюджет Аргус 14,5 млн", "бюджет проекта аргус"
    ) == pytest.approx(2 / 3)
    assert score_relevance("что-то другое", "бюджет проекта аргус") == 0.0
    assert score_relevance("anything", "по до") is None  # no meaningful terms

    records = [_rec("Бюджет Аргус 14,5 млн"), _rec("Аргус: выбран кабель Гюрза")]
    out = apply_recall(records, "какой бюджет проекта аргус?", 5, min_relevance=0.5)
    assert [r.text for r in out] == ["Бюджет Аргус 14,5 млн"]


def test_temporal_decay_prefers_fresh_fact() -> None:
    now = datetime.now(tz=timezone.utc)
    old = _rec(
        "Бюджет Аргус 12 млн",
        meta={"created_at": (now - timedelta(days=60)).isoformat()},
    )
    fresh = _rec(
        "Бюджет Аргус 14,5 млн",
        meta={"created_at": (now - timedelta(days=1)).isoformat()},
    )
    # Newest-first store order intentionally inverted to prove decay reorders.
    out = apply_recall([old, fresh], "бюджет аргус", 2, half_life_seconds=30 * 86400)
    assert out[0].text == "Бюджет Аргус 14,5 млн"


def test_raw_fallback_ranks_below_slotted_fact() -> None:
    raw = _rec("Бюджет Аргус обсуждали долго", meta={"source": "raw_fallback"})
    fact = _rec("Бюджет Аргус 14,5 млн", meta={"slot": "argus-budget"})
    out = apply_recall([raw, fact], "бюджет аргус", 2)
    assert out[0].text == "Бюджет Аргус 14,5 млн"


def test_sanitize_memory_text_strips_recall_block() -> None:
    text = (
        "Recalled memory from earlier sessions (reference only — NOT part of "
        "this conversation...):\n- старый факт раз\n- старый факт два\n"
        "Реальный ответ ассистента."
    )
    assert sanitize_memory_text(text) == "Реальный ответ ассистента."
    assert sanitize_memory_text("обычный текст") == "обычный текст"


def test_render_frame_states_reference_and_trust_newest() -> None:
    from agent_driver.memory.provider import RecallResult

    block = render_recall_block(RecallResult(session_id="s", records=[_rec("факт")]))
    assert "NOT part of this conversation" in block
    assert "trust the newest" in block


def test_render_frame_states_staleness_guard() -> None:
    """Epic M3: the recall frame tells the model memory may be stale — verify
    a recalled fact against the current situation before acting on it."""
    from agent_driver.memory.provider import RecallResult

    block = render_recall_block(RecallResult(session_id="s", records=[_rec("факт")]))
    lowered = block.lower()
    assert "unverified" in lowered or "out of date" in lowered
    assert "verify" in lowered


class _ScriptedProvider(FakeProvider):
    """Returns scripted completions in order."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__(response_text="")
        self._replies = list(replies)
        self.calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        text = self._replies.pop(0) if self._replies else "[]"
        response = await super().complete(request)
        # Epic 036: extraction reads the forced tool-call channel. A JSON-array
        # reply becomes an ``emit_result`` call; prose emits no tool call.
        import json

        meta = dict(response.message.metadata or {})
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                meta["planned_tool_calls"] = [
                    {"name": "emit_result", "args": {"facts": parsed}}
                ]
        except (json.JSONDecodeError, TypeError):
            pass
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


@pytest.mark.asyncio
async def test_extraction_retries_once_on_prose_then_parses() -> None:
    """deepseek-class flake: prose first, valid JSON on the nudged retry."""
    provider = _ScriptedProvider(
        [
            "Вот факты, которые стоит запомнить: пользователь любит таблицы.",
            '[{"text": "Пользователь предпочитает сводки таблицей", "slot": "answer-format"}]',
        ]
    )
    store = InMemoryMemoryStore()
    memory = FactExtractingMemoryProvider(store, provider)
    await memory.sync_turn(
        MemoryTurn(
            session_id="s", user_text="запомни: сводки таблицей", assistant_text="ок"
        )
    )
    assert provider.calls == 2
    records = store.list_for_session("s")
    assert len(records) == 1
    assert records[0].kind == MemoryKind.FACT
    assert records[0].metadata.get("slot") == "answer-format"


@pytest.mark.asyncio
async def test_extraction_falls_back_raw_after_two_bad_replies() -> None:
    provider = _ScriptedProvider(["не json", "снова не json"])
    store = InMemoryMemoryStore()
    memory = FactExtractingMemoryProvider(store, provider)
    await memory.sync_turn(
        MemoryTurn(session_id="s", user_text="вопрос", assistant_text="ответ")
    )
    assert provider.calls == 2
    records = store.list_for_session("s")
    assert records and all(r.metadata.get("source") == "raw_fallback" for r in records)


@pytest.mark.asyncio
async def test_provider_min_relevance_gate_via_prefetch() -> None:
    store = InMemoryMemoryStore()
    provider = FactExtractingMemoryProvider(
        store, FakeProvider(response_text="[]"), recall_min_relevance=0.34
    )
    store.append(_rec("Пользователь предпочитает сводки таблицей"))
    result = await provider.prefetch(
        RecallQuery(session_id="s", query="когда была встреча маркетинга?")
    )
    assert result.records == []
