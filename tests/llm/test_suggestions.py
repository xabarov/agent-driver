"""Epic 038: suggested next questions — reject filter, cost-gate, generation."""

from __future__ import annotations

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.suggestions import (
    filter_suggestion,
    generate_suggestions,
    suppress_reason_for_usage,
)

# --- reject filter -------------------------------------------------------------


def test_filter_keeps_a_real_follow_up_question() -> None:
    q = "Кто владелец решения по деплою офиса?"
    assert filter_suggestion(q) == q


@pytest.mark.parametrize(
    "bad",
    [
        "",
        "   ",
        "спасибо",  # evaluative
        "Отлично, выглядит хорошо",  # evaluative
        "Давайте я покажу action items",  # assistant-voice
        "Let me show the action items",  # assistant-voice EN
        "Вот следующий вопрос",  # assistant-voice
        "Нет вопросов для предложения",  # meta
        "nothing to suggest here",  # meta EN
        "(нет очевидного вопроса)",  # meta-wrapped
        "Вопрос: какой бюджет?",  # prefixed label
        "API error: rate limited",  # error echo
        "Какой бюджет? И ещё: кто ответственный по срокам поставки кабеля?",  # multi-sentence
        "как",  # too few words (1)
        "**Какой бюджет проекта?**",  # formatting
        "Кто именно из всех участников этой встречи в итоге отвечал за согласование финального бюджета сделки покупки офиса и когда это было решено",  # too many words
    ],
)
def test_filter_rejects_junk(bad: str) -> None:
    assert filter_suggestion(bad) is None


def test_filter_allows_trailing_single_question_mark() -> None:
    # A single question ending in "?" is fine (not treated as multi-sentence).
    q = "Какой срок оплаты по сделке?"
    assert filter_suggestion(q) == q


# --- cost-gate -----------------------------------------------------------------


def test_cost_gate_suppresses_expensive_uncached_turn() -> None:
    usage = {
        "input_tokens": 8000,
        "cache_creation_input_tokens": 3000,
        "output_tokens": 500,
    }
    assert suppress_reason_for_usage(usage) == "cache_cold"


def test_cost_gate_allows_cheap_turn_and_none() -> None:
    assert (
        suppress_reason_for_usage({"prompt_tokens": 400, "completion_tokens": 120})
        is None
    )
    assert suppress_reason_for_usage(None) is None


# --- generation ----------------------------------------------------------------


class _EmitProvider(FakeProvider):
    """Emits a scripted ``emit_result`` tool call with a questions payload."""

    def __init__(self, questions: list[str]) -> None:
        super().__init__(response_text="")
        self._questions = questions

    async def complete(self, request: LlmRequest) -> LlmResponse:
        response = await super().complete(request)
        meta = dict(response.message.metadata or {})
        meta["planned_tool_calls"] = [
            {"name": "emit_result", "args": {"questions": self._questions}}
        ]
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


@pytest.mark.asyncio
async def test_generate_filters_dedups_and_caps() -> None:
    provider = _EmitProvider(
        [
            "Какой срок оплаты по сделке?",
            "Какой срок оплаты по сделке",  # dup (punctuation-insensitive)
            "спасибо",  # filtered (evaluative)
            "Кто владелец решения по деплою?",
            "Какая сумма сделки указана в отчёте?",  # 4th valid → capped out
        ]
    )
    out = await generate_suggestions(
        provider=provider,
        question="Что решили по офису?",
        answer="Решили купить 6-й этаж за 5 млн.",
        max_suggestions=3,
    )
    assert out == [
        "Какой срок оплаты по сделке?",
        "Кто владелец решения по деплою?",
        "Какая сумма сделки указана в отчёте?",
    ]


@pytest.mark.asyncio
async def test_generate_empty_when_question_or_answer_blank() -> None:
    provider = _EmitProvider(["Какой срок оплаты?"])
    assert await generate_suggestions(provider=provider, question="", answer="x") == []
    assert (
        await generate_suggestions(provider=provider, question="x", answer="  ") == []
    )


@pytest.mark.asyncio
async def test_generate_suppressed_by_cost_gate() -> None:
    provider = _EmitProvider(["Какой срок оплаты по сделке?"])
    out = await generate_suggestions(
        provider=provider,
        question="Что решили?",
        answer="Купили офис.",
        usage={"input_tokens": 20000},
    )
    assert out == []


@pytest.mark.asyncio
async def test_generate_never_raises_on_generation_error() -> None:
    class _BoomProvider(FakeProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            raise RuntimeError("provider down")

    out = await generate_suggestions(
        provider=_BoomProvider(response_text=""),
        question="Что решили?",
        answer="Купили офис.",
    )
    assert out == []


@pytest.mark.asyncio
async def test_generate_all_filtered_returns_empty_not_junk() -> None:
    provider = _EmitProvider(["спасибо", "Вот вопрос", "nothing to suggest"])
    out = await generate_suggestions(
        provider=provider, question="Что решили?", answer="Купили офис."
    )
    assert out == []
