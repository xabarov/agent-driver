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
                "unique_domains": ["example.com", "example.org"],
            },
            "artifacts": {"paths": ["research/report.md"]},
            "research_efficiency": {
                "deep_research_artifact_expected": True,
                "first_tool": "todo_write",
                "long_final_after_report": False,
                "output_tokens_after_first_report_update": 7,
                "report_update_count": 1,
            },
        },
        failures=[],
        workspace_artifacts={
            "artifacts": [{"path": "research/report.md", "kind": "report"}],
        },
        workspace_preview={
            "content": "# Report\nBody",
            "truncated": False,
        },
    )

    assert (
        "tool_chain: `todo_write -> web_search -> web_fetch -> file_write`" in scorecard
    )
    assert "after_report=`7`" in scorecard
    assert "domains=`2`" in scorecard
    assert "workspace=`research/report.md`" in scorecard
    assert "first_tool=`todo_write`" in scorecard
