from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_live_probe_module():
    path = Path(__file__).resolve().parent / "e2e" / "chat_live_probe.py"
    spec = importlib.util.spec_from_file_location("chat_live_probe_for_test", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_research_budget_stop_waits_until_budget_is_exhausted() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="budget",
        prompt="budget",
        min_research_domain_count=2,
        max_research_search_count_without_min_domains=10,
        max_research_fetch_count_without_min_domains=10,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research": {
                "search_count": 9,
                "fetch_count": 9,
                "unique_domains": ["example.com"],
            }
        },
    )

    assert reason is None


def test_research_budget_stop_detects_search_loop_before_diversity() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="budget",
        prompt="budget",
        min_research_domain_count=2,
        max_research_search_count_without_min_domains=10,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research": {
                "search_count": 10,
                "fetch_count": 1,
                "unique_domains": ["example.com"],
            }
        },
    )

    assert reason == (
        "research search budget exhausted before source diversity: "
        "10 searches, 1 domains"
    )


def test_research_budget_stop_detects_fetch_loop_before_diversity() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="budget",
        prompt="budget",
        min_research_domain_count=2,
        max_research_fetch_count_without_min_domains=10,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research": {
                "search_count": 1,
                "fetch_count": 10,
                "unique_domains": ["example.com"],
            }
        },
    )

    assert reason == (
        "research fetch budget exhausted before source diversity: "
        "10 fetches, 1 domains"
    )


def test_research_budget_stop_allows_budget_after_diversity() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="budget",
        prompt="budget",
        min_research_domain_count=2,
        max_research_search_count_without_min_domains=10,
        max_research_fetch_count_without_min_domains=10,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research": {
                "search_count": 100,
                "fetch_count": 100,
                "unique_domains": ["example.com", "example.org"],
            }
        },
    )

    assert reason is None


def test_research_budget_stop_detects_unknown_tool_call() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(name="deep", prompt="deep research")

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "failures": {"unknown_tool_call": True},
            "unknown_tools": {"names": ["artifacts_list"]},
        },
    )

    assert "unknown tool call detected" in str(reason)


def test_research_budget_stop_detects_phase_violation_budget() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep",
        prompt="deep research",
        require_research_efficiency=True,
        max_phase_violations_before_stop=4,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {"research_efficiency": {"phase_violation_count": 5}},
    )

    assert reason == "deep research phase violation budget exhausted: 5 > 4"


def test_research_budget_stop_detects_token_runaway_before_report_projection() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep",
        prompt="deep research",
        require_research_efficiency=True,
        max_tokens_before_report_projection=60000,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research_efficiency": {
                "total_tokens": 60001,
                "report_trace_update_seen": False,
                "report_write_seen": False,
            }
        },
    )

    assert (
        reason
        == "deep research token budget exhausted before report projection: 60001 > 60000"
    )


def test_research_budget_stop_detects_unexpected_tool_after_child_synthesis() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep",
        prompt="deep research",
        require_research_efficiency=True,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research_efficiency": {
                "report_trace_update_seen": False,
                "report_write_seen": False,
            },
            "subagents": {
                "child_synthesis_pending": True,
                "unexpected_tool_after_child_synthesis_pending": "agent_tool",
            },
        },
    )

    assert reason == (
        "deep research parent synthesis contract violated after child join: "
        "agent_tool"
    )


def test_research_budget_stop_detects_medium_subagent_fanout() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep",
        prompt="deep research",
        research_profile="medium",
        require_research_efficiency=True,
    )

    reason = live_probe.research_budget_stop_reason(
        scenario,
        {
            "research_efficiency": {},
            "subagents": {"runs_started": 3},
        },
    )

    assert reason == "deep research subagent fan-out budget exhausted: 3 > 2"


def test_render_scenario_scorecard_includes_research_efficiency_fields() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep-research-artifact",
        prompt="deep research",
    )

    scorecard = live_probe.render_scenario_scorecard(
        scenario=scenario,
        summary={
            "run_id": "run_1",
            "verdict": "pass",
            "terminal_event": "run_completed",
            "tool_chain": "todo_write -> web_search -> web_fetch -> file_write",
            "final_readiness": "allowed",
            "llm": {
                "usage": {
                    "input_tokens": 10,
                    "output_tokens": 20,
                    "total_tokens": 30,
                }
            },
            "research": {
                "search_count": 1,
                "fetch_count": 2,
                "fetch_attempt_count": 2,
                "unique_domains": ["example.com", "example.org"],
            },
            "artifacts": {"paths": ["research/report.md", "research/sources.jsonl"]},
            "research_efficiency": {
                "deep_research_artifact_expected": True,
                "deep_research_phase": "final",
                "phase_violation_count": 0,
                "report_status": "verified",
                "verified_read_count": 2,
                "search_budget_status": "within_initial",
                "repeated_search_query_count": 0,
                "first_tool": "todo_write",
                "long_final_after_report": False,
                "full_report_rewrite": False,
                "stale_report_edit": False,
                "repeated_report_read": False,
                "final_references_report_artifact": True,
                "output_tokens_after_first_report_update": 7,
                "report_update_count": 1,
                "report_full_write_count": 1,
                "report_targeted_edit_without_fresh_read_count": 0,
                "repeated_unchanged_report_read_count": 0,
                "source_ledger_record_count": 2,
            },
        },
        failures=[],
        workspace_artifacts={
            "artifacts": [
                {"path": "research/report.md", "kind": "report"},
                {"path": "research/sources.jsonl", "kind": "research"},
            ],
        },
        workspace_preview={
            "content": "# Report\nBody",
            "truncated": False,
        },
        health_status={
            "tracing": {
                "enabled": True,
                "configured": True,
                "error": None,
            }
        },
    )

    assert (
        "tool_chain: `todo_write -> web_search -> web_fetch -> file_write`" in scorecard
    )
    assert "after_report=`7`" in scorecard
    assert "attempts=`2`" in scorecard
    assert "domains=`2`" in scorecard
    assert "workspace=`research/report.md, research/sources.jsonl`" in scorecard
    assert "full_writes=`1`" in scorecard
    assert "stale_edits=`0`" in scorecard
    assert "repeat_reads=`0`" in scorecard
    assert "source_records=`2`" in scorecard
    assert "report_projection: workspace_exists=`True`" in scorecard
    assert "trace_path_seen=`True`" in scorecard
    assert "trace_update_seen=`False`" in scorecard
    assert "write_seen=`False`" in scorecard
    assert "status=`verified`" in scorecard
    assert "phase=`final`" in scorecard
    assert "phase_violations=`0`" in scorecard
    assert "verified=`2`" in scorecard
    assert "search_budget=`within_initial`" in scorecard
    assert "repeat_queries=`0`" in scorecard
    assert "final_refs_report=`True`" in scorecard
    assert "first_tool=`todo_write`" in scorecard
    assert "phoenix: enabled=`True`, configured=`True`, error=`-`" in scorecard


def test_reconcile_workspace_artifact_failures_marks_projection_mismatch() -> None:
    live_probe = _load_live_probe_module()
    scenario = live_probe.LiveScenario(
        name="deep",
        prompt="deep research",
        required_artifact_path="research/report.md",
    )

    failures = live_probe.reconcile_workspace_artifact_failures(
        scenario=scenario,
        summary={
            "artifacts": {"paths": ["research/sources.jsonl"]},
            "research_efficiency": {"missing_report_artifact": True},
        },
        failures=[
            "failure flag is set: deep_research_no_report_artifact",
            "research report artifact is missing",
            "required artifact missing from trace: research/report.md",
            "summary verdict is 'fail'",
        ],
        workspace_artifacts={
            "artifacts": [
                {"path": "research/report.md", "kind": "report"},
                {"path": "research/sources.jsonl", "kind": "research"},
            ],
        },
    )

    assert "research report artifact is missing" not in failures
    assert "required artifact missing from trace: research/report.md" not in failures
    assert "summary verdict is 'fail'" in failures
    assert any("report artifact projection mismatch" in item for item in failures)
