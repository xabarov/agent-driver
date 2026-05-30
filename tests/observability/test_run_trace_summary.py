"""Tests for scenario-oriented run trace summaries."""

from __future__ import annotations

from agent_driver.observability import summarize_run_trace


def _completed_tool(name: str) -> dict[str, object]:
    tool: dict[str, object] = {"tool_name": name, "status": "completed"}
    if name == "agent_tool":
        tool["args"] = {
            "description": "Verify delegated facts",
            "task": "Check delegated facts and return a concise grounded summary.",
        }
    return {
        "event": "tool_call_completed",
        "data": {
            "tools": [tool],
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


def test_trace_summary_does_not_require_research_for_plan_only_search_plan() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="составь только план поиска информации по истории Fender, без реферата",
        assistant_text="План готов.",
        events=[
            _completed_tool("todo_write"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research"]["required"] is False
    assert summary["failures"]["missing_required_research_evidence"] is False
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


def test_trace_summary_collects_subagent_markers() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Поручи часть работы субагенту и дай итог",
        assistant_text="Итог с учетом ответа субагента.",
        events=[
            _completed_tool("agent_tool"),
            {"event": "subagent_group_started", "data": {"group_id": "group_1"}},
            {"event": "subagent_started", "data": {"task_id": "task_1"}},
            {
                "event": "subagent_completed",
                "data": {"task_id": "task_1", "status": "completed"},
            },
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["groups_started"] == 1
    assert summary["subagents"]["agent_tool_used"] is True
    assert summary["subagents"]["delegation_requested"] is True
    assert summary["subagents"]["runs_completed"] == 1
    assert summary["subagents"]["join_states"] == ["done"]
    assert summary["subagents"]["parent_synthesized_final"] is True
    assert summary["verdict"] == "pass"


def test_trace_summary_flags_missed_explicit_delegation() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Поручи субагенту проверить факты и дай итог",
        assistant_text="Я сам проверил факты и даю итог.",
        events=[{"event": "run_completed", "data": {}}],
    )

    assert summary["verdict"] == "fail"
    assert summary["subagents"]["delegation_requested"] is True
    assert summary["failures"]["missed_explicit_delegation"] is True


def test_trace_summary_flags_unnecessary_delegation_for_simple_prompt() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сколько букв в слове test",
        assistant_text="В слове test четыре буквы.",
        events=[
            _completed_tool("agent_tool"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["unnecessary_delegation"] is True


def test_trace_summary_flags_subagent_without_parent_synthesis() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Поручи субагенту собрать факты и дай итог",
        assistant_text="Субагент завершился. Сейчас подготовлю итог.",
        events=[
            _completed_tool("agent_tool"),
            {"event": "subagent_group_started", "data": {"group_id": "group_1"}},
            {"event": "subagent_started", "data": {"task_id": "task_1"}},
            {
                "event": "subagent_completed",
                "data": {"task_id": "task_1", "status": "completed"},
            },
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["subagent_no_final"] is True
    assert summary["failures"]["child_result_not_used"] is True


def test_trace_summary_flags_unbounded_child_prompt() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Поручи субагенту собрать факты и дай итог",
        assistant_text="Итог с учетом ответа субагента.",
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "status": "completed",
                            "args": {"task": "проверь"},
                        }
                    ]
                },
            },
            {"event": "subagent_group_started", "data": {"group_id": "group_1"}},
            {"event": "subagent_started", "data": {"task_id": "task_1"}},
            {
                "event": "subagent_completed",
                "data": {"task_id": "task_1", "status": "completed"},
            },
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["child_prompt_not_bounded"] is True


def test_trace_summary_respects_no_search_instruction() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Собери по памяти факты о Fender, без поиска в интернете",
        assistant_text="Итог по памяти.",
        task_contract={
            "kind": "research",
            "requires_research": True,
        },
        events=[{"event": "run_completed", "data": {}}],
    )

    assert summary["research"]["required"] is False
    assert summary["failures"]["missing_required_research_evidence"] is False
    assert summary["verdict"] == "pass"


def test_trace_summary_collects_control_markers() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Найди источник",
        assistant_text="Итог.",
        events=[
            {
                "event": "control_requested",
                "data": {"kind": "enqueue_user_message", "priority": "next"},
            },
            {
                "event": "command_queued",
                "data": {"kind": "enqueue_user_message", "priority": "next"},
            },
            {
                "event": "command_dequeued",
                "data": {"kind": "enqueue_user_message", "priority": "next"},
            },
            {
                "event": "control_applied",
                "data": {"kind": "enqueue_user_message", "priority": "next"},
            },
            _completed_tool("web_search"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["controls"]["queued"] == 1
    assert summary["controls"]["dequeued"] == 1
    assert summary["controls"]["applied"] == 1
    assert summary["controls"]["semantic_routes"] == ["queue_after_next_boundary"]
