"""Tests for scenario-oriented run trace summaries."""

from __future__ import annotations

from typing import Any, cast

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
                    "tool_choice_effective": {"type": "tool", "name": "web_search"},
                    "request_allowed_tools": ["web_search"],
                    "request_tool_names": ["web_search"],
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
    assert summary["llm"]["request_allowed_tools"] == [["web_search"]]
    assert summary["llm"]["request_tool_names"] == [["web_search"]]


def test_trace_summary_does_not_double_count_started_and_completed_tools() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Открой https://example.com и дай итог со ссылкой",
        assistant_text="Итог: [Example](https://example.com).",
        events=[
            {
                "event": "tool_call_started",
                "data": {
                    "tool_name": "web_fetch",
                    "args": {"url": "https://example.com"},
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "web_fetch",
                    "status": "completed",
                    "args": {"url": "https://example.com"},
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["tool_names"] == ["web_fetch"]
    assert summary["tool_chain"] == "web_fetch"
    assert summary["tool_calls"] == 1
    assert summary["research"]["fetch_count"] == 1
    assert summary["research"]["unique_domains"] == ["example.com"]


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


def test_trace_summary_counts_legacy_exit_plan_mode_alias() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Реализуй изменение",
        assistant_text="План готов.",
        events=[
            _completed_tool("enter_plan_mode"),
            _completed_tool("exit_plan_mode"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["planning"]["approval_cycles"] == 1
    assert summary["planning"]["exit_plan_mode_calls"] == 1


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


def test_trace_summary_splits_parent_and_child_research_evidence() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "subagent_completed",
                "data": {
                    "task_id": "task_1",
                    "status": "completed",
                    "child_evidence": {
                        "search_count": 3,
                        "fetch_count": 2,
                        "verified_read_count": 1,
                    },
                },
            },
            _completed_tool("web_fetch"),
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/sources.jsonl", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    research = summary["research_efficiency"]
    assert research["parent_fetch_count"] == 1
    assert research["child_search_count"] == 3
    assert research["child_fetch_count"] == 2
    assert research["child_verified_read_count"] == 1
    assert summary["subagents"]["child_fetch_count"] == 2


def test_trace_summary_projects_child_orchestration_metrics() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
        },
        events=[
            {
                "event": "subagent_started",
                "data": {
                    "task_id": "task_1",
                    "task": "Collect source evidence for model A.",
                },
            },
            {
                "event": "subagent_started",
                "data": {
                    "task_id": "task_2",
                    "task": "Collect source evidence for model A.",
                },
            },
            {
                "event": "subagent_completed",
                "data": {
                    "task_id": "task_1",
                    "status": "completed",
                    "used_tools": ["web_search", "web_fetch"],
                    "summary": "Found one source.",
                    "child_evidence": {
                        "verified_read_count": 1,
                        "candidate_count": 2,
                        "blocked_read_count": 1,
                    },
                },
            },
        ],
    )

    subagents = summary["subagents"]
    research = summary["research_efficiency"]
    assert subagents["child_count"] == 2
    assert subagents["duplicated_child_queries"] == 1
    assert subagents["child_tool_names"] == ["web_fetch", "web_search"]
    assert subagents["child_source_records"] == 4
    assert research["child_source_records"] == 4


def test_trace_summary_flags_hard_browser_action_without_opt_in() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
            "hard_options": {"allow_browser_action": False},
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("browser_action"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["hard_profile"] is True
    assert summary["research_efficiency"]["hard_browser_action_without_opt_in"] is True
    assert summary["failures"]["deep_research_browser_action_without_opt_in"] is True


def test_trace_summary_flags_hard_browser_read_before_source_read() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
            "hard_options": {"allow_browser_action": False},
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("browser_read"),
            _completed_tool("source_read"),
            {"event": "run_completed", "data": {}},
        ],
    )

    ladder = summary["research_efficiency"]["hard_source_ladder"]
    assert ladder["browser_read_count"] == 1
    assert ladder["source_read_count"] == 1
    assert ladder["browser_used_before_source_read"] is True
    assert summary["failures"]["deep_research_browser_used_before_source_read"] is True


def test_trace_summary_requires_browser_read_fallback_reason() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
            "hard_options": {"allow_browser_action": False},
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("source_read"),
            _completed_tool("browser_read"),
            {"event": "run_completed", "data": {}},
        ],
    )

    ladder = summary["research_efficiency"]["hard_source_ladder"]
    assert ladder["browser_read_missing_fallback_reason"] is True
    assert (
        summary["failures"]["deep_research_browser_read_missing_fallback_reason"]
        is True
    )


def test_trace_summary_accepts_browser_read_fallback_reason() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
            "hard_options": {"allow_browser_action": False},
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("source_read"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "browser_read",
                            "status": "completed",
                            "args": {
                                "url": "https://example.com/js",
                                "fallback_reason": "source_read returned empty body",
                            },
                            "structured_output": {
                                "url": "https://example.com/js",
                                "fallback_reason": "source_read returned empty body",
                            },
                        }
                    ]
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    ladder = summary["research_efficiency"]["hard_source_ladder"]
    assert ladder["browser_read_missing_fallback_reason"] is False
    assert (
        summary["failures"]["deep_research_browser_read_missing_fallback_reason"]
        is False
    )


def test_trace_summary_flags_missing_hard_claims_artifact() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 1},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["hard_claims_missing"] is True
    assert summary["failures"]["deep_research_hard_claims_missing"] is True


def test_trace_summary_counts_hard_claims_artifact_records() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 1},
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/claims.jsonl",
                    "record_count": 2,
                    "verified_count": 2,
                    "unsupported_count": 0,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["artifacts"]["claims_record_count"] == 2
    assert summary["artifacts"]["claims_verified_count"] == 2
    assert summary["research_efficiency"]["hard_claims_missing"] is False
    assert summary["research_efficiency"]["hard_claims_empty"] is False
    assert summary["research_efficiency"]["hard_claims_no_verified"] is False
    assert summary["research_efficiency"]["hard_claims_unsupported"] is False
    assert summary["failures"]["deep_research_hard_claims_missing"] is False
    assert summary["failures"]["deep_research_hard_claims_empty"] is False
    assert summary["failures"]["deep_research_hard_claims_no_verified"] is False
    assert summary["failures"]["deep_research_hard_claims_unsupported"] is False


def test_trace_summary_rejects_hard_claims_without_verified_rows() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use hard Deep Research.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 1},
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/claims.jsonl",
                    "record_count": 1,
                    "verified_count": 0,
                    "unsupported_count": 1,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["hard_claims_no_verified"] is True
    assert summary["research_efficiency"]["hard_claims_unsupported"] is True
    assert summary["failures"]["deep_research_hard_claims_no_verified"] is True
    assert summary["failures"]["deep_research_hard_claims_unsupported"] is True


def test_trace_summary_clears_child_synthesis_pending_after_parent_report_write() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "file_write",
                    "record_count": 1,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["child_synthesis_pending"] is False
    assert summary["subagents"]["parent_synthesized_final"] is True
    assert summary["subagents"]["tools_after_child_synthesis_pending"] == ["file_write"]
    assert summary["subagents"]["unexpected_tool_after_child_synthesis_pending"] is None
    assert summary["failures"]["child_result_not_used"] is False


def test_trace_summary_clears_child_synthesis_pending_when_report_write_precedes_marker() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["parent_synthesized_final"] is True
    assert summary["subagents"]["child_synthesis_pending"] is False
    assert summary["failures"]["child_result_not_used"] is False


def test_trace_summary_counts_artifact_handoff_when_marker_lands_after_report() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/sources.jsonl"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "file_write",
                    "record_count": 2,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["parent_synthesized_final"] is True
    assert summary["subagents"]["child_synthesis_pending"] is False
    assert summary["subagents"]["tools_after_child_synthesis_pending"] == ["file_write"]
    assert summary["failures"]["subagent_no_final"] is False
    assert summary["failures"]["child_result_not_used"] is False


def test_trace_summary_does_not_count_pre_child_report_via_late_marker_fallback() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/sources.jsonl"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "file_write",
                    "record_count": 2,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["parent_synthesized_final"] is False
    assert summary["failures"]["child_result_not_used"] is True


def test_trace_summary_does_not_count_failed_report_write_as_parent_artifact() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "failed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "file_write",
                    "record_count": 1,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["artifacts"]["report_write_seen"] is False
    assert summary["research_efficiency"]["report_write_seen"] is False
    assert summary["subagents"]["child_synthesis_pending"] is True


def test_trace_summary_does_not_count_pre_child_report_write_as_child_synthesis() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            _completed_tool("agent_tool"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["subagents"]["parent_synthesized_final"] is False
    assert summary["failures"]["child_result_not_used"] is True


def test_trace_summary_allows_phase05_artifact_handoff_despite_stale_todos() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text=(
            "Deep Research report is ready at `research/report.md`.\n"
            '<tool_call>{"name":"read_file","arguments":{}}</tool_call>'
        ),
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            _completed_tool("web_search"),
            {
                "event": "subagent_group_joined",
                "data": {"group_id": "group_1", "join_state": "done"},
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "source_ledger",
                    "record_count": 1,
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "planning_state",
                "data": {
                    "todos": [
                        {
                            "id": "fetch",
                            "content": "Fetch and verify sources",
                            "status": "pending",
                        }
                    ]
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["failures"]["text_form_tool_call"] is False
    assert summary["failures"]["plan_todos_incomplete_on_final"] is False
    assert summary["failures"]["search_only_research_report"] is False
    assert summary["deep_research_artifact_handoff_complete"] is True
    assert summary["verdict"] == "pass"


def test_trace_summary_surfaces_candidate_only_source_quality() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "source_ledger_updated",
                "data": {
                    "search_candidates": [{"url": "https://example.com/a"}],
                    "verified_reads": [],
                    "blocked_reads": [],
                    "failed_reads": [],
                },
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "source_ledger",
                    "record_count": 1,
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    quality = summary["research_efficiency"]["source_quality"]
    assert summary["research_efficiency"]["quality_ok"] is False
    assert summary["research_efficiency"]["quality_status"] == "candidate_only"
    assert quality["verified_read_count"] == 0
    assert quality["candidate_count"] == 1


def test_trace_summary_surfaces_verified_source_quality() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "source_ledger_updated",
                "data": {
                    "search_candidates": [],
                    "verified_reads": [{"url": "https://example.com/a"}],
                    "blocked_reads": [],
                    "failed_reads": [],
                },
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "source_ledger",
                    "record_count": 1,
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["quality_ok"] is True
    assert summary["research_efficiency"]["quality_status"] == "verified"
    assert summary["research_efficiency"]["report_status"] == "verified"


def test_trace_summary_requires_report_reference_for_artifact_handoff() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Done.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "source_ledger",
                    "record_count": 1,
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["final_references_report_artifact"] is False
    assert summary["failures"]["deep_research_final_missing_report_reference"] is True
    assert summary["deep_research_artifact_handoff_complete"] is False


def test_trace_summary_requires_child_synthesis_for_phase05_handoff() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            {
                "event": "source_ledger_updated",
                "data": {
                    "search_candidates": [{"url": "https://candidate.example/a"}],
                    "verified_reads": [],
                    "failed_reads": [],
                    "blocked_reads": [],
                },
            },
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "source_ledger",
                    "record_count": 1,
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["deep_research_artifact_handoff_complete"] is False
    assert summary["failures"]["deep_research_low_verified_coverage"] is True


def test_trace_summary_allows_parent_artifact_writes_after_subagent() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/sources.jsonl"},
                        }
                    ]
                },
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_updated",
                "data": {"path": "research/sources.jsonl", "tool_name": "file_write"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["phase_violation"] is False
    assert summary["failures"]["deep_research_phase_violation"] is False


def test_trace_summary_allows_parent_file_write_without_args_after_subagent() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            _completed_tool("file_write"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["phase_violation"] is False


def test_trace_summary_allows_parent_verify_fetch_in_write_phase() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Report ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("web_fetch"),
            _completed_tool("web_fetch"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "status": "completed",
                            "args": {"path": "research/report.md"},
                        }
                    ]
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["phase_violation"] is False
    assert summary["failures"]["deep_research_phase_violation"] is False


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


def test_trace_summary_allows_bounded_research_child_task_without_description() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "status": "completed",
                            "args": {
                                "task": (
                                    "Find source notes and URLs for the parent "
                                    "Deep Research report."
                                )
                            },
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
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {
                "event": "research_progress",
                "data": {
                    "kind": "deep_research_child_synthesis_pending",
                    "pending": True,
                    "summary_chars": 200,
                },
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/report.md", "tool_name": "file_write"},
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "tool_name": "file_write",
                    "record_count": 2,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["failures"]["child_prompt_not_bounded"] is False


def test_trace_summary_rejects_generic_research_child_task_without_description() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "status": "completed",
                            "args": {
                                "task": (
                                    "Research everything about distributed systems "
                                    "and tell me what matters."
                                )
                            },
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
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["failures"]["child_prompt_not_bounded"] is True


def test_trace_summary_allows_bounded_research_child_task_alias() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Use Deep Research and write a report.",
        assistant_text="Deep Research report is ready at `research/report.md`.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "status": "completed",
                            "args": {
                                "instructions": (
                                    "Find source URLs and compact notes for the "
                                    "parent report."
                                )
                            },
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
                "data": {"group_id": "group_1", "join_state": "joined"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["failures"]["child_prompt_not_bounded"] is False


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


def test_trace_summary_explains_context_pressure_recommendation() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Сделай длинное исследование",
        assistant_text="Итог.",
        events=[
            {
                "event": "warning",
                "data": {
                    "kind": "token_pressure",
                    "signal_id": "context_delegate_or_summarize",
                    "severity": "warning",
                    "state": "delegate_or_summarize",
                    "recommendation": "delegate_or_summarize",
                    "context_usage_ratio": 0.46,
                },
            },
            _completed_tool("agent_tool"),
            {"event": "run_completed", "data": {}},
        ],
    )

    pressure = summary["context_pressure"]
    assert pressure["states"] == ["delegate_or_summarize"]
    assert pressure["recommendations"] == ["delegate_or_summarize"]
    assert pressure["delegated_after_recommendation"] is True
    assert pressure["ignored_latest_recommendation"] is False


def test_trace_summary_flags_ignored_context_pressure_recommendation() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Сделай длинное исследование",
        assistant_text="Итог.",
        events=[
            {
                "event": "warning",
                "data": {
                    "kind": "token_pressure",
                    "signal_id": "context_compact_recommended",
                    "severity": "warning",
                    "state": "compact_recommended",
                    "recommendation": "compact_recommended",
                    "context_usage_ratio": 0.75,
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    pressure = summary["context_pressure"]
    latest = cast(dict[str, Any], pressure["latest"])
    assert latest.get("state") == "compact_recommended"
    assert pressure["compaction_attempted_after_recommendation"] is False
    assert pressure["ignored_latest_recommendation"] is True


def test_trace_summary_allows_agent_tool_verify_recovery() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "status": "completed",
                            "args": {"query": "fork join queue"},
                        }
                    ]
                },
            },
            _completed_tool("agent_tool"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ]
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.org/b"},
                        }
                    ]
                },
            },
            {
                "event": "source_ledger_updated",
                "data": {
                    "verified_reads": [
                        {"url": "https://example.com/a"},
                        {"url": "https://example.org/b"},
                    ],
                    "failed_reads": [],
                    "blocked_reads": [],
                    "search_candidates": [],
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 2},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["phase_violation"] is False
    assert summary["failures"]["deep_research_phase_violation"] is False


def test_trace_summary_treats_blocked_deep_fetches_as_fallback_coverage() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "medium",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ]
                },
            },
            {
                "event": "source_ledger_updated",
                "data": {
                    "verified_reads": [],
                    "failed_reads": [],
                    "blocked_reads": [
                        {"url": "https://example.com/a", "status": "blocked"}
                    ],
                    "search_candidates": [],
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 1},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["report_status"] == "fallback"
    assert summary["research_efficiency"]["low_verified_coverage"] is False
    assert summary["failures"]["deep_research_low_verified_coverage"] is False
    assert summary["failures"]["deep_research_preliminary_final"] is False


def test_trace_summary_counts_hard_read_tools_as_fetch_progress() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text=(
            "Готово: полный отчет сохранен в research/report.md. "
            "[A](https://example.com/a) [B](https://example.org/b)"
        ),
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
            "research_mode": "deep",
            "research_profile": "hard",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "source_read",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                            "structured_output": {"url": "https://example.com/a"},
                        }
                    ]
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "pdf_read",
                            "status": "completed",
                            "args": {"url": "https://example.org/b"},
                            "structured_output": {
                                "url": "https://example.org/b",
                                "verified_text": True,
                            },
                        }
                    ]
                },
            },
            {
                "event": "source_ledger_updated",
                "data": {
                    "verified_reads": [
                        {"url": "https://example.com/a"},
                        {"url": "https://example.org/b"},
                    ],
                    "failed_reads": [],
                    "blocked_reads": [],
                    "search_candidates": [],
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl", "record_count": 2},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research"]["fetch_count"] == 2
    assert summary["research_efficiency"]["fetch_attempt_count"] == 2
    assert summary["research_efficiency"]["phase_violation"] is False
