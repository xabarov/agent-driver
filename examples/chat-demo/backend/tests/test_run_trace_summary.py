"""Tests for scenario-oriented run trace summaries."""

from __future__ import annotations

from app.services.run_trace_summary import summarize_run_trace

from agent_driver.observability.message_metadata import (
    aggregate_message_metadata_from_events,
)


def _completed_tool(
    name: str,
    *,
    args: dict[str, object] | None = None,
) -> dict[str, object]:
    tool: dict[str, object] = {"tool_name": name, "status": "completed"}
    if args is not None:
        tool["args"] = args
    return {
        "event": "tool_call_completed",
        "data": {
            "tools": [tool],
        },
    }


def _completed_python(result: str = "3") -> dict[str, object]:
    return {
        "event": "tool_call_completed",
        "data": {
            "tools": [
                {
                    "tool_name": "python",
                    "status": "completed",
                    "result_summary": result,
                }
            ],
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


def test_trace_summary_exposes_provider_profile() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="hello",
        assistant_text="hello",
        events=[
            {
                "event": "llm_call_completed",
                "data": {
                    "provider": "openrouter",
                    "model": "openai/gpt-5.5",
                    "provider_profile": {
                        "provider_id": "openrouter",
                        "model_id": "openai/gpt-5.5",
                        "supports_reasoning": True,
                    },
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["provider_profile"] == {
        "provider_id": "openrouter",
        "model_id": "openai/gpt-5.5",
        "supports_reasoning": True,
    }


def test_trace_summary_passes_research_with_web_tool() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Найди в интернете источник и дай итоговый ответ",
        assistant_text="Итоговый ответ со ссылкой.",
        events=[
            _completed_tool("web_search"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["research"]["tools_used"] == ["web_search"]


def test_trace_summary_exposes_tool_chain_usage_and_artifact_updates() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="подготовь deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "size_bytes": 4096,
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "size_bytes": 1024,
                    "record_count": 3,
                    "tool_name": "source_ledger",
                },
            },
            {
                "event": "llm_call_completed",
                "data": {
                    "usage": {
                        "input_tokens": 100,
                        "output_tokens": 40,
                        "total_tokens": 140,
                        "cost_usd_estimate": 0.002,
                    }
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["tool_chain"] == "todo_write -> web_search -> file_write"
    assert summary["llm"]["usage"]["total_tokens"] == 140
    assert summary["artifacts"]["update_count"] == 2
    assert summary["artifacts"]["report_updated"] is True
    assert summary["artifacts"]["source_ledger_updated"] is True
    assert summary["artifacts"]["source_ledger_record_count"] == 3
    assert summary["artifacts"]["paths"] == [
        "research/report.md",
        "research/sources.jsonl",
    ]
    assert summary["research_efficiency"]["first_tool"] == "todo_write"
    assert summary["research_efficiency"]["missing_source_ledger_artifact"] is False
    assert summary["research_efficiency"]["report_full_write_count"] == 1
    assert summary["research_efficiency"]["full_report_rewrite"] is False
    assert (
        summary["research_efficiency"]["output_tokens_after_first_report_update"] == 40
    )


def test_trace_summary_flags_deep_research_without_report_artifact() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай глубокий research report по очередям fork-join",
        assistant_text="Краткий отчет без файла.",
        events=[
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research_efficiency"]["deep_research_artifact_expected"] is True
    assert summary["research_efficiency"]["missing_report_artifact"] is True
    assert summary["failures"]["deep_research_no_report_artifact"] is True
    assert summary["failures"]["deep_research_missing_initial_todo"] is True


def test_trace_summary_flags_deep_research_without_source_ledger_artifact() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research_efficiency"]["missing_report_artifact"] is False
    assert summary["research_efficiency"]["missing_source_ledger_artifact"] is True
    assert summary["failures"]["deep_research_no_source_ledger_artifact"] is True


def test_trace_summary_flags_deep_research_final_without_report_reference() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: вот краткие выводы по теме.",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research_efficiency"]["final_references_report_artifact"] is False
    assert summary["research_efficiency"]["final_missing_report_reference"] is True
    assert summary["failures"]["deep_research_final_missing_report_reference"] is True


def test_trace_summary_allows_deep_research_final_report_reference() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["final_references_report_artifact"] is True
    assert summary["research_efficiency"]["final_missing_report_reference"] is False
    assert summary["failures"]["deep_research_final_missing_report_reference"] is False


def test_trace_summary_allows_agent_tool_in_deep_research() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("agent_tool"),
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

    assert summary["research_efficiency"]["unexpected_agent_tool"] is False
    assert summary["failures"]["deep_research_unexpected_agent_tool"] is False


def test_trace_summary_flags_denied_skill_tool_in_deep_research() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "skill_tool",
                            "status": "denied",
                            "result_summary": (
                                "path outside workspace "
                                "(/workspace/session): /workspace/agent_driver/skills/curated"
                            ),
                        }
                    ]
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

    assert summary["research_efficiency"]["skill_denied"] is True
    assert summary["failures"]["deep_research_skill_denied"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_deep_research_report_with_only_candidates() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            {
                "event": "source_ledger_updated",
                "data": {
                    "search_candidates": [
                        {"url": "https://candidate.example/a"},
                        {"url": "https://candidate.example/b"},
                    ],
                    "verified_reads": [],
                    "failed_reads": [],
                    "blocked_reads": [],
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

    assert summary["research_efficiency"]["report_status"] == "draft"
    assert summary["research_efficiency"]["candidate_count"] == 2
    assert summary["research_efficiency"]["verified_read_count"] == 0
    assert summary["research_efficiency"]["low_verified_coverage"] is True
    assert summary["research_efficiency"]["preliminary_final"] is True
    assert summary["failures"]["deep_research_low_verified_coverage"] is True
    assert summary["failures"]["deep_research_preliminary_final"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_repeated_deep_research_search_args() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Черновик сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool(
                "web_search",
                args={"query": "fork join queueing models"},
            ),
            _completed_tool(
                "web_search",
                args={"query": "  Fork   Join Queueing Models  "},
            ),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["repeated_search_args"] is True
    assert summary["research_efficiency"]["repeated_search_query_count"] == 1
    assert summary["failures"]["deep_research_repeated_search_args"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_deep_research_search_without_fetch_progress() -> None:
    events = [_completed_tool("todo_write")]
    for index in range(7):
        events.append(
            _completed_tool(
                "web_search",
                args={"query": f"fork join queueing models topic {index}"},
            )
        )
    events.append({"event": "run_completed", "data": {}})

    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Черновик сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=events,
    )

    assert summary["research_efficiency"]["search_budget_status"] == "expanded"
    assert summary["research_efficiency"]["search_without_fetch_progress"] is True
    assert summary["failures"]["deep_research_search_without_fetch_progress"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_deep_research_tool_entropy_high() -> None:
    events = [_completed_tool("todo_write")]
    for index in range(16):
        events.append(
            _completed_tool(
                "web_search",
                args={"query": f"fork join queueing models branch {index}"},
            )
        )
    events.append({"event": "run_completed", "data": {}})

    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Черновик сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=events,
    )

    assert summary["research_efficiency"]["search_budget_status"] == "over_hard_cap"
    assert summary["research_efficiency"]["tool_entropy_high"] is True
    assert summary["failures"]["deep_research_tool_entropy_high"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_allows_adaptive_deep_research_search_expansion() -> None:
    events = [_completed_tool("todo_write")]
    for index in range(7):
        events.append(
            _completed_tool(
                "web_search",
                args={"query": f"fork join queueing models source {index}"},
            )
        )
    events.extend(
        [
            _completed_tool("web_fetch", args={"url": "https://example.com/a"}),
            {
                "event": "source_ledger_updated",
                "data": {
                    "search_candidates": [],
                    "verified_reads": [{"url": "https://example.com/a"}],
                    "failed_reads": [],
                    "blocked_reads": [],
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
        ]
    )

    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=events,
    )

    assert summary["research_efficiency"]["search_budget_status"] == "expanded"
    assert summary["research_efficiency"]["search_without_fetch_progress"] is False
    assert summary["research_efficiency"]["tool_entropy_high"] is False
    assert summary["failures"]["deep_research_search_without_fetch_progress"] is False
    assert summary["failures"]["deep_research_tool_entropy_high"] is False


def test_trace_summary_exposes_deep_research_phase_from_terminal_contract() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch", args={"url": "https://example.com/a"}),
            {
                "event": "source_ledger_updated",
                "data": {
                    "verified_reads": [{"url": "https://example.com/a"}],
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
                "data": {"path": "research/sources.jsonl", "record_count": 1},
            },
            {
                "event": "run_completed",
                "data": {
                    "metadata": {
                        "research_session_contract": {
                            "deep_research": {
                                "phase": "final",
                                "next_allowed_tools": [],
                            }
                        }
                    }
                },
            },
        ],
    )

    assert summary["research_efficiency"]["deep_research_phase"] == "final"
    assert (
        summary["research_efficiency"]["deep_research_phase_next_allowed_tools"] == []
    )


def test_trace_summary_allows_expected_deep_research_phase_sequence() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово: полный отчет сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search", args={"query": "fork join queue"}),
            _completed_tool("web_fetch", args={"url": "https://example.com/a"}),
            _completed_tool("web_fetch", args={"url": "https://example.org/b"}),
            _completed_tool("file_write", args={"path": "research/report.md"}),
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
    assert summary["research_efficiency"]["phase_violation_count"] == 0
    assert summary["failures"]["deep_research_phase_violation"] is False


def test_trace_summary_flags_deep_research_phase_violation() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Черновик сохранен в research/report.md.",
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search", args={"query": "fork join queue"}),
            _completed_tool("file_write", args={"path": "research/report.md"}),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["phase_violation"] is True
    assert summary["research_efficiency"]["phase_violation_count"] == 1
    assert summary["research_efficiency"]["phase_violations"][0]["phase"] == "verify"
    assert summary["research_efficiency"]["phase_violations"][0]["tool_name"] == (
        "file_write"
    )
    assert summary["failures"]["deep_research_phase_violation"] is True
    assert summary["verdict"] == "fail"


def test_trace_summary_flags_repeated_full_report_write() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            _completed_tool("file_write"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research_efficiency"]["report_full_write_count"] == 2
    assert summary["research_efficiency"]["full_report_rewrite"] is True
    assert summary["failures"]["deep_research_full_report_rewrite"] is True


def test_trace_summary_allows_report_append_after_initial_write() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            _completed_tool("file_write"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "append",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["report_full_write_count"] == 1
    assert summary["research_efficiency"]["full_report_rewrite"] is False
    assert summary["failures"]["deep_research_full_report_rewrite"] is False


def test_trace_summary_flags_stale_report_patch_without_read() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            _completed_tool("file_patch"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "patch",
                    "tool_name": "file_patch",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research_efficiency"]["report_targeted_edit_count"] == 1
    assert (
        summary["research_efficiency"]["report_targeted_edit_without_fresh_read_count"]
        == 1
    )
    assert summary["research_efficiency"]["stale_report_edit"] is True
    assert summary["failures"]["deep_research_stale_report_edit"] is True


def test_trace_summary_allows_report_patch_after_fresh_read() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "artifact_preview",
                    "status": "completed",
                    "args": {"path": "research/report.md"},
                },
            },
            _completed_tool("file_patch"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "patch",
                    "tool_name": "file_patch",
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["report_targeted_edit_count"] == 1
    assert (
        summary["research_efficiency"]["report_targeted_edit_without_fresh_read_count"]
        == 0
    )
    assert summary["research_efficiency"]["stale_report_edit"] is False
    assert summary["failures"]["deep_research_stale_report_edit"] is False
    assert summary["research_efficiency"]["report_patch_count"] == 1


def test_trace_summary_tracks_long_chat_before_report_artifact() -> None:
    long_chunk = "длинный черновик до файла. " * 80
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            {"event": "token_delta", "data": {"delta_text": long_chunk}},
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "tool_name": "file_write",
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert (
        summary["research_efficiency"]["long_chat_before_report_chars"]
        == len(long_chunk)
    )
    assert (
        summary["research_efficiency"]["first_report_update_before_long_chat"]
        is False
    )


def test_trace_summary_allows_report_artifact_before_long_chat() -> None:
    long_chunk = "длинный текст уже после файла. " * 80
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "tool_name": "file_write",
                },
            },
            {"event": "token_delta", "data": {"delta_text": long_chunk}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["long_chat_before_report_chars"] == 0
    assert (
        summary["research_efficiency"]["first_report_update_before_long_chat"]
        is True
    )


def test_trace_summary_flags_repeated_unchanged_report_read() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "artifact_preview",
                    "status": "completed",
                    "args": {"path": "research/report.md"},
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "artifact_read",
                    "status": "completed",
                    "args": {"path": "research/report.md"},
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["repeated_unchanged_report_read_count"] == 1
    assert summary["research_efficiency"]["repeated_report_read"] is True
    assert summary["failures"]["deep_research_repeated_report_read"] is True


def test_trace_summary_allows_report_read_after_intervening_write() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово, отчет в research/report.md",
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("web_fetch"),
            _completed_tool("file_write"),
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "write",
                    "mode": "overwrite",
                    "tool_name": "file_write",
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "artifact_preview",
                    "status": "completed",
                    "args": {"path": "research/report.md"},
                },
            },
            _completed_tool("file_patch"),
            {
                "event": "artifact_updated",
                "data": {
                    "path": "research/report.md",
                    "kind": "report",
                    "operation": "patch",
                    "tool_name": "file_patch",
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tool_name": "artifact_preview",
                    "status": "completed",
                    "args": {"path": "research/report.md"},
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/sources.jsonl",
                    "kind": "research",
                    "operation": "write",
                    "record_count": 2,
                    "tool_name": "source_ledger",
                },
            },
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["repeated_unchanged_report_read_count"] == 0
    assert summary["research_efficiency"]["repeated_report_read"] is False
    assert summary["failures"]["deep_research_repeated_report_read"] is False


def test_trace_summary_flags_long_final_after_report_artifact() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет",
        assistant_text="Готово.\n" + ("длинный финальный дубль. " * 120),
        events=[
            _completed_tool("todo_write"),
            _completed_tool("web_search"),
            _completed_tool("file_write"),
            {
                "event": "artifact_updated",
                "data": {"path": "research/report.md", "operation": "write"},
            },
            {
                "event": "llm_call_completed",
                "data": {
                    "usage": {
                        "input_tokens": 500,
                        "output_tokens": 900,
                        "total_tokens": 1400,
                    }
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research_efficiency"]["long_final_after_report"] is True
    assert summary["failures"]["deep_research_long_final_after_report"] is True
    assert (
        summary["research_efficiency"]["output_tokens_after_first_report_update"] == 900
    )


def test_trace_summary_flags_search_only_report_research() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt=(
            "составь todo лист и иди по нему. Мне нужно поискать информацию "
            "в интернете о fork-join моделях массового обслуживания"
        ),
        assistant_text="Краткий обзор без проверки страниц.",
        events=[
            _completed_tool("web_search"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research"]["depth"] == "source_verified_report"
    assert summary["research"]["fetch_required_but_missing"] is True
    assert summary["research"]["final_readiness"] == "repair_needed"
    assert "missing_fetched_sources" in summary["repair_required_reasons"]
    assert summary["failures"]["search_only_research_report"] is True


def test_trace_summary_requires_fetch_for_explicit_open_url_request() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt=(
            "найди источник про fork-join queueing models и открой один "
            "найденный URL"
        ),
        assistant_text="Краткий итог со ссылкой https://example.com/a.",
        events=[
            _completed_tool("web_search"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research"]["depth"] == "light_search"
    assert summary["research"]["fetch_required"] is True
    assert summary["research"]["fetch_required_but_missing"] is True
    assert "missing_fetched_sources" in summary["repair_required_reasons"]


def test_trace_summary_passes_report_research_with_fetched_sources() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="напиши подробный отчет по найденным источникам",
        assistant_text=(
            "Итог по двум источникам: "
            "[A](https://example.com/a), [B](https://example.org/b)."
        ),
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ],
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
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["research"]["fetch_count"] == 2
    assert summary["research"]["unique_domains"] == ["example.com", "example.org"]
    assert summary["research"]["final_has_source_links"] is True
    assert summary["research"]["final_missing_source_links"] is False


def test_trace_summary_marks_missing_final_links_as_research_diagnostic() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="напиши подробный отчет по найденным источникам",
        assistant_text="Итог по двум источникам без встроенных ссылок.",
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ],
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
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research"]["final_has_source_links"] is False
    assert summary["research"]["final_missing_source_links"] is True
    assert summary["final_readiness"] == "repair_needed"
    assert summary["repair_required_reasons"] == ["final_missing_source_links"]
    assert summary["failures"]["final_missing_source_links"] is True


def test_trace_summary_ignores_blocked_fetches_for_research_depth() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="напиши подробный отчет по найденным источникам",
        assistant_text="Итог с [источником](https://example.com/a).",
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        },
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "result_summary": "web_fetch blocked by upstream HTTP 403",
                            "args": {"url": "https://blocked.example/b"},
                        },
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research"]["fetch_count"] == 1
    assert summary["research"]["unique_domains"] == ["example.com"]
    assert summary["failures"]["search_only_research_report"] is True


def test_trace_summary_allows_blocked_fetch_fallback_for_deep_research() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="сделай deep research отчет по найденным источникам",
        assistant_text=(
            "Источники заблокировали чтение: https://example.com/blocked-a и "
            "https://example.org/blocked-b. Полный отчет сохранен в research/report.md."
        ),
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "status_code": 403,
                            "result_summary": "web_fetch blocked by upstream HTTP 403",
                            "args": {"url": "https://example.com/blocked-a"},
                        },
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "status_code": 403,
                            "result_summary": "web_fetch blocked by upstream HTTP 403",
                            "args": {"url": "https://example.org/blocked-b"},
                        },
                    ],
                },
            },
            {
                "event": "artifact_created",
                "data": {
                    "path": "research/report.md",
                    "tool_name": "file_write",
                    "tool_call_id": "write_report",
                },
            },
            {
                "event": "artifact_created",
                "data": {"path": "research/sources.jsonl"},
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["research"]["fetch_count"] == 0
    assert summary["research"]["fetch_attempt_count"] == 2
    assert summary["research"]["failed_fetch_count"] == 2
    assert summary["research"]["fetch_fallback_required"] is True
    assert summary["failures"]["search_only_research_report"] is False
    assert summary["final_readiness"] == "allowed"


def test_message_metadata_collects_tool_source_evidence() -> None:
    metadata = aggregate_message_metadata_from_events(
        [
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "sources": [
                                {
                                    "id": "web_search:call_search:1",
                                    "url": "https://example.com/source",
                                    "canonical_url": "https://example.com/source",
                                    "source_type": "web_search",
                                    "title": "Search hit",
                                    "rank": 1,
                                }
                            ],
                        },
                        {
                            "tool_name": "web_fetch",
                            "sources": [
                                {
                                    "id": "web_fetch:call_fetch:1",
                                    "url": "https://example.com/source",
                                    "canonical_url": "https://example.com/source",
                                    "source_type": "web_fetch",
                                    "title": "Fetched source",
                                    "rank": 1,
                                }
                            ],
                        },
                    ]
                },
            },
        ]
    )

    assert metadata["source_evidence"] == [
        {
            "id": "web_fetch:call_fetch:1",
            "url": "https://example.com/source",
            "canonical_url": "https://example.com/source",
            "source_type": "web_fetch",
            "title": "Fetched source",
            "rank": 1,
        }
    ]


def test_message_metadata_ignores_failed_tool_source_evidence() -> None:
    metadata = aggregate_message_metadata_from_events(
        [
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "result_summary": "web_fetch blocked by upstream HTTP 403",
                            "sources": [
                                {
                                    "id": "web_fetch:call_fetch:1",
                                    "url": "https://example.com/blocked",
                                    "canonical_url": "https://example.com/blocked",
                                    "source_type": "web_fetch",
                                    "title": "Blocked source",
                                    "rank": 1,
                                }
                            ],
                        }
                    ]
                },
            },
        ]
    )

    assert "source_evidence" not in metadata


def test_trace_summary_flags_insufficient_report_source_diversity() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="напиши подробный отчет по найденным источникам",
        assistant_text="Итог по двум страницам одного сайта.",
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ],
                },
            },
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/b"},
                        }
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["research"]["insufficient_source_diversity"] is True
    assert summary["failures"]["insufficient_research_source_diversity"] is True


def test_trace_summary_reports_effective_prompt_surface() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="привет",
        assistant_text="Привет!",
        events=[
            {
                "event": "llm_call_completed",
                "data": {
                    "effective_tool_names": ["python", "agent_tool"],
                    "prompt_fragments": [
                        "react_chat_tool_policy_python.txt",
                        "react_chat_tool_policy_subagents.txt",
                    ],
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["prompt_surface"] == {
        "effective_tool_names": ["python", "agent_tool"],
        "prompt_fragments": [
            "react_chat_tool_policy_python.txt",
            "react_chat_tool_policy_subagents.txt",
        ],
    }


def test_trace_summary_flags_unknown_tool_calls() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="исследуй тему",
        assistant_text="Итог.",
        events=[
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "thought",
                            "status": "denied",
                            "error_code": "tool_not_registered",
                            "result_summary": "Tool 'thought' is not registered.",
                        }
                    ]
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["unknown_tools"]["names"] == ["thought"]
    assert summary["failures"]["unknown_tool_call"] is True


def test_trace_summary_flags_incomplete_plan_on_final() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="составь todo и иди по нему",
        assistant_text="Итоговый ответ.",
        events=[
            {
                "event": "tool_call_completed",
                "data": {"tools": [{"tool_name": "todo_write", "status": "completed"}]},
            },
            {
                "event": "run_completed",
                "data": {
                    "planning_snapshot": {
                        "todos": [
                            {"id": "a", "content": "A", "status": "completed"},
                            {"id": "b", "content": "B", "status": "pending"},
                        ],
                        "completed": 1,
                        "total": 2,
                    }
                },
            },
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["failures"]["plan_todos_incomplete_on_final"] is True
    assert summary["final_readiness"] == "repair_needed"
    assert summary["repair_required_reasons"] == ["unfinished_todos"]


def test_trace_summary_allows_sourced_research_final_to_cover_process_todos() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt=(
            "составь todo лист и иди по нему. Нужно поискать информацию "
            "в интернете и подготовить отчет"
        ),
        assistant_text=(
            "Итоговый отчет на основе проверенных источников. "
            "Я сопоставил найденные материалы, выделил основные выводы, "
            "объяснил практическое применение и указал ограничения. "
            "Ключевой вывод: модель применима для анализа задержек и "
            "производительности в системах с параллельными ветвями выполнения. "
            "Источники: [A](https://example.com/a), [B](https://example.org/b)."
        ),
        events=[
            _completed_tool("web_search"),
            {
                "event": "tool_call_completed",
                "data": {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "status": "completed",
                            "args": {"url": "https://example.com/a"},
                        }
                    ],
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
                    ],
                },
            },
            {
                "event": "run_completed",
                "data": {
                    "planning_snapshot": {
                        "todos": [
                            {
                                "id": "search",
                                "content": "Search",
                                "status": "completed",
                            },
                            {
                                "id": "analyze",
                                "content": "Analyze sources",
                                "status": "in_progress",
                            },
                            {
                                "id": "final",
                                "content": "Write final report",
                                "status": "pending",
                            },
                        ],
                        "completed": 1,
                        "total": 3,
                    }
                },
            },
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["failures"]["plan_todos_incomplete_on_final"] is False
    assert summary["final_readiness"] == "allowed"
    assert summary["repair_required_reasons"] == []


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


def test_trace_summary_separates_provider_failure_after_planning() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Составь план и выполни поиск",
        assistant_text="Run failed",
        events=[
            _completed_tool("todo_write"),
            {"event": "llm_request_rejected", "data": {"status_code": 429}},
            {"event": "run_failed", "data": {"status_code": 429}},
        ],
    )

    assert summary["provider_rejected"] is True
    assert summary["planning"]["verdict"] == "fabricated"
    assert summary["failures"]["run_failed_or_cancelled"] is True
    assert summary["failures"]["fabricated_planning"] is False


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


def test_trace_summary_does_not_fail_missing_python_from_prompt_heuristic() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Сколько букв r в strawberry? Проверь точно.",
        assistant_text="В слове strawberry три буквы r.",
        events=[
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["python"]["python_expected"] is False
    assert summary["python"]["missed_python_for_calculation"] is False
    assert summary["failures"]["missed_python"] is False


def test_trace_summary_does_not_require_python_for_research_about_calculation_domain() -> (
    None
):
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt=(
            "Найди информацию о fork-join моделях массового обслуживания "
            "и их применении для расчета компьютерных сетей"
        ),
        assistant_text="Краткий обзор.",
        events=[
            _completed_tool("web_search"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["python"]["python_expected"] is False
    assert summary["failures"]["missed_python"] is False


def test_trace_summary_passes_python_calculation_with_final_answer() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Сколько букв r в strawberry? Проверь точно.",
        assistant_text="В слове strawberry 3 буквы r.",
        events=[
            _completed_python("3"),
            {"event": "llm_call_completed", "data": {}},
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["python"]["python_tool_used"] is True
    assert summary["python"]["python_result_observed"] is True
    assert summary["python"]["final_after_python"] is True


def test_trace_summary_flags_repeated_python_policy_errors() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="Посчитай статистику",
        assistant_text="Не удалось из-за политики sandbox.",
        events=[
            _completed_python("python policy: imports blocked by sandbox (os)"),
            _completed_python("unauthorized import 'os'"),
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "fail"
    assert summary["python"]["python_policy_errors"] == 2
    assert summary["failures"]["python_policy_loop"] is True


def test_trace_summary_reports_compaction_lifecycle() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="продолжай длинную задачу",
        assistant_text="Продолжаю после сжатия контекста.",
        events=[
            {
                "event": "memory_compaction_started",
                "data": {
                    "compaction_id": "cmp_1",
                    "mode": "partial",
                    "token_pressure_state": "blocking",
                },
            },
            {
                "event": "memory_compacted",
                "data": {
                    "outcome": "successful",
                    "mode": "partial",
                    "compaction_id": "cmp_1",
                    "summarized_message_count": 8,
                    "compaction_state": {"circuit_breaker_open": False},
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["verdict"] == "pass"
    assert summary["compaction"]["attempts"] == 1
    assert summary["compaction"]["started"] == 1
    assert summary["compaction"]["successful"] == 1
    assert summary["compaction"]["failed"] == 0
    assert summary["compaction"]["skipped"] == 0
    assert summary["compaction"]["modes"] == ["partial"]
    assert summary["compaction"]["latest"]["summarized_message_count"] == 8


def test_trace_summary_reports_compaction_skipped_and_failed() -> None:
    summary = summarize_run_trace(
        run_id="run_test",
        user_prompt="продолжай",
        assistant_text="Ответ.",
        events=[
            {
                "event": "memory_compacted",
                "data": {
                    "outcome": "skipped",
                    "mode": "none",
                    "skip_reason": "not_eligible",
                    "compaction_state": {"circuit_breaker_open": False},
                },
            },
            {
                "event": "memory_compaction_started",
                "data": {"compaction_id": "cmp_2", "mode": "llm_full"},
            },
            {
                "event": "memory_compacted",
                "data": {
                    "outcome": "failed",
                    "mode": "llm_full",
                    "compaction_id": "cmp_2",
                    "failure_kind": "llm_compaction_failed",
                    "compaction_state": {"circuit_breaker_open": True},
                },
            },
            {"event": "run_completed", "data": {}},
        ],
    )

    assert summary["compaction"]["attempts"] == 1
    assert summary["compaction"]["started"] == 1
    assert summary["compaction"]["successful"] == 0
    assert summary["compaction"]["failed"] == 1
    assert summary["compaction"]["skipped"] == 1
    assert summary["compaction"]["modes"] == ["none", "llm_full"]
    assert summary["compaction"]["circuit_breaker_open"] is True
    assert summary["compaction"]["latest"]["failure_kind"] == "llm_compaction_failed"
