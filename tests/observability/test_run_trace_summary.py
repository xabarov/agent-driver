"""Tests for scenario-oriented run trace summaries."""

from __future__ import annotations

from agent_driver.observability import summarize_run_trace


def _completed_tool(name: str) -> dict[str, object]:
    return {
        "event": "tool_call_completed",
        "data": {
            "tools": [{"tool_name": name, "status": "completed"}],
        },
    }


def test_trace_summary_flags_missing_research_tool() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Найди в интернете источник и дай итоговый ответ",
        assistant_text="Готово: вот итог без источников.",
        events=[
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research"]["required"] is True
    assert summary["failures"]["missing_required_research_evidence"] is True


def test_trace_summary_passes_research_with_web_tool() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Найди в интернете источник и дай итоговый ответ",
        assistant_text="Итоговый ответ со ссылкой.",
        events=[
            {
                "event": "llm_call_started",
                "data": {
                    "tool_choice_effective": {"type": "tool", "name": "web_search"}
                },
            },
            _completed_tool("web_search"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["research"]["tools_used"] == ["web_search"]
    assert summary["llm"]["tool_choice_effective"] == [
        {"type": "tool", "name": "web_search"}
    ]


def test_trace_summary_flags_progress_only_final() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Напиши реферат",
        assistant_text="Теперь я приступаю к структурированию материала для реферата.",
        events=[{"event": "run_completed", "data": {}}],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["progress_only_final"] is True


def test_trace_summary_flags_plain_text_tool_call() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Посчитай через python",
        assistant_text='{"name":"python","arguments":{"code":"print(2+2)"}}',
        events=[{"event": "run_completed", "data": {}}],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["text_form_tool_call"] is True


def test_trace_summary_flags_fabricated_planning() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Составь план и выполни",
        assistant_text="План готов.",
        events=[
            _completed_tool("todo_write"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["planning"]["verdict"] == "fabricated"
    assert summary["failures"]["fabricated_planning"] is True


def test_trace_summary_allows_resolved_clarification_interrupt() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Уточни тему, если нужно",
        assistant_text="Спасибо, продолжаю с выбранной темой.",
        events=[
            {
                "event": "interrupt_requested",
                "data": {"reason": "clarification_required"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["interrupts"] == ["clarification_required"]
    assert summary["failures"]["stuck_on_interrupt"] is False
    assert summary["verdict"] == "pass"


def test_trace_summary_flags_stuck_interrupt() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Уточни тему, если нужно",
        assistant_text="",
        events=[
            {
                "event": "interrupt_requested",
                "data": {"reason": "clarification_required"},
            },
        ],
    )

    assert summary["failures"]["stuck_on_interrupt"] is True
    assert summary["failures"]["missing_terminal_event"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_allows_plan_only_without_data_tools() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Составь только план работ",
        assistant_text="План готов: 1. Исследовать 2. Проверить 3. Описать.",
        events=[
            _completed_tool("todo_write"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["planning"]["verdict"] == "fabricated"
    assert summary["failures"]["fabricated_planning"] is False
    assert summary["verdict"] == "pass"


def test_trace_summary_flags_repeated_approval_planning() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Реализуй изменение",
        assistant_text="План снова готов.",
        events=[
            _completed_tool("enter_plan_mode"),
            _completed_tool("exit_plan_mode_v2"),
            _completed_tool("enter_plan_mode"),
            _completed_tool("exit_plan_mode_v2"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["planning"]["approval_cycles"] == 2
    assert summary["failures"]["repeated_approval_planning"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_extra_question_for_research_deliverable() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Найди в интернете и напиши реферат",
        assistant_text="Какой аспект выбрать?",
        events=[
            _completed_tool("ask_user_question"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["failures"]["extra_ask_user_question"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_collects_runtime_markers() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Напиши итог",
        assistant_text="Итог.",
        events=[
            {
                "event": "llm_call_started",
                "data": {
                    "force_final_reason": "deliverable_request",
                    "continuation_reason": "progress_only_final",
                    "tool_choice_effective": "none",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {
                "event": "run_completed",
                "data": {"force_final_reason": "deliverable_request"},
            },
        ],
    )

    assert summary["llm"]["force_final_reasons"] == ["deliverable_request"]
    assert summary["llm"]["continuation_reasons"] == ["progress_only_final"]
    assert summary["runtime_markers"]["force_final_reasons"] == ["deliverable_request"]
