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
