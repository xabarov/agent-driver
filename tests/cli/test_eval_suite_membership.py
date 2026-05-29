"""Tests for eval suite composition and scenario contracts."""

from __future__ import annotations

from agent_driver.cli.evals import live_scenarios_for_suite


def test_deep_suite_contains_only_problematic_and_new_scenarios() -> None:
    """Deep suite should stay slim and focused."""
    ids = {row.scenario_id for row in live_scenarios_for_suite("deep")}
    assert ids == {
        "sandbox_build_verify",
        "file_edit_minimal_patch",
        "bash_denial_recovery",
        "loop_detection_force_final",
        "workspace_cwd_relative_paths",
        "web_zero_results_honest_finalize",
        "todo_status_lifecycle",
        "multi_file_rename",
        "python_sandbox_arithmetic",
        "forbidden_bash_governance",
        "multi_file_summary_digest",
        "chat_multi_turn_followup",
        "ambiguous_request_clarify_then_act",
        "real_refactor_small_module",
    }


def test_regression_suite_contains_stable_regression_targets() -> None:
    """Regression suite should keep known stable multi-step scenarios."""
    ids = {row.scenario_id for row in live_scenarios_for_suite("regression")}
    assert {"repo_audit_report", "web_to_repo_migration_plan"} <= ids
    assert "interrupt_resume_file_write" in ids
    assert "python_gamma_stdlib_only" in ids
    assert "python_gamma_scipy" in ids
    assert "python_pandas_linalg" in ids


def test_default_smoke_suite_is_small_risk_subset() -> None:
    ids = {row.scenario_id for row in live_scenarios_for_suite("default_smoke")}
    assert ids == {
        "news_web_search",
        "url_summary",
        "repo_lookup",
        "multi_step_research",
        "dangerous_tool_request",
    }


def test_eval_scenarios_have_consistent_tool_contracts() -> None:
    """All suites should keep required/expected/forbidden contracts coherent."""
    for suite in ("default", "deep", "regression"):
        for row in live_scenarios_for_suite(suite):
            required = set(row.required_tools)
            expected = set(row.expected_tools)
            forbidden = set(row.forbidden_tools)
            assert required <= expected
            assert not (expected & forbidden)
            if row.sandbox_required:
                assert row.tool_packs


def test_all_suite_includes_regression_scenarios() -> None:
    """All suite should include both deep and regression groups."""
    ids = {row.scenario_id for row in live_scenarios_for_suite("all")}
    assert "sandbox_build_verify" in ids
    assert "file_edit_minimal_patch" in ids
    assert "repo_audit_report" in ids
    assert "web_to_repo_migration_plan" in ids
