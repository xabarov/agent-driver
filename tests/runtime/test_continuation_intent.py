"""Continuation-intent detection (epic 015 Phase B/D: odd-fence false positive)."""

from __future__ import annotations

from agent_driver.runtime.single_agent.lifecycle.continuation import (
    analyze_continuation_intent,
)


def test_complete_answer_with_odd_fences_ending_in_prose_is_not_continuation() -> None:
    """Root cause of the MeetScript over-iteration: a complete answer that merely uses an odd number of
    ``` fences was flagged `unclosed_code_block` and re-prompted. It must read as finished."""
    text = (
        "Вот сводка по каждой встрече:\n"
        "1. Встреча про ```офис``` — обсуждение.\n"
        "2. Встреча про ```такси и логистику — план.\n\n"
        "Итого сводка охватывает все встречи. Если нужны детали, могу углубиться с помощью инструментов."
    )
    assert text.count("```") % 2 == 1  # odd fence count
    result = analyze_continuation_intent(text)
    assert result.should_continue is False, result.reason


def test_truncated_mid_code_block_is_continuation() -> None:
    text = "Here is the fix:\n```python\ndef foo(bar):\n    return bar +"
    result = analyze_continuation_intent(text)
    assert result.should_continue is True
    assert result.reason == "unclosed_code_block"


def test_balanced_code_block_answer_is_not_continuation() -> None:
    text = "Done. Example:\n```python\nprint('ok')\n```\nThat covers it."
    assert analyze_continuation_intent(text).should_continue is False


def test_unfinished_suffix_is_continuation() -> None:
    assert (
        analyze_continuation_intent("I computed the totals and").should_continue is True
    )
    assert analyze_continuation_intent("Я посчитал итоги и").should_continue is True


def test_continuation_signal_preserved() -> None:
    assert (
        analyze_continuation_intent(
            "Готово частично. Now I will fetch the rest"
        ).should_continue
        is True
    )


def test_first_person_next_step_is_still_continuation() -> None:
    result = analyze_continuation_intent(
        "Промежуточные данные собраны. Мой следующий шаг — запустить проверку TLS"
    )
    assert result.should_continue is True
    assert result.reason == "continuation_signal"


def test_report_next_step_heading_is_not_continuation() -> None:
    text = (
        "Проверка завершена. Подтверждена уязвимость обхода пути. "
        "Ограничение покрытия: проверены только доступные HTTP-маршруты.\n\n"
        "Безопасный следующий шаг: разработчикам нормализовать путь; "
        "тестировщикам повторить негативный сценарий после исправления."
    )
    result = analyze_continuation_intent(text)
    assert result.should_continue is False, result.reason


def test_english_report_next_step_heading_is_not_continuation() -> None:
    text = (
        "Assessment complete. One path traversal issue was confirmed. "
        "Recommended next step is to normalize the path and rerun the negative test."
    )
    result = analyze_continuation_intent(text)
    assert result.should_continue is False, result.reason


def test_plain_complete_answer_is_not_continuation() -> None:
    assert (
        analyze_continuation_intent(
            "The most popular topic is neural networks."
        ).should_continue
        is False
    )
