"""Strategy comparison runner tests for context compaction."""

from __future__ import annotations

from agent_driver.evals import (
    render_context_compaction_report,
    run_context_compaction_strategy_comparison,
)


def test_context_compaction_strategy_runner_returns_rows() -> None:
    """Runner should produce all Phase 8 strategy rows."""
    rows = run_context_compaction_strategy_comparison()
    names = {row.strategy for row in rows}
    assert "trim_only" in names
    assert "trim_plus_microcompaction" in names
    assert "session_memory_compaction" in names
    assert "full_llm_compaction" in names
    assert "partial_compaction" in names


def test_context_compaction_report_is_markdown_table() -> None:
    """Report renderer should output markdown table header."""
    report = render_context_compaction_report()
    assert "| strategy | recall |" in report
    assert "provider_cost" in report
    assert "trim_only" in report
