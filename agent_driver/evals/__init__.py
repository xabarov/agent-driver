"""Deterministic evaluation harness, dataset runners, and baseline reports."""

from agent_driver.evals.baseline import compare_reports
from agent_driver.evals.contracts import (
    BudgetLimits,
    CaseEvaluation,
    DatasetCase,
    EvalReport,
    EvaluatorResult,
    ReportComparison,
)
from agent_driver.evals.evaluators import (
    default_evaluators,
    evaluate_checkpoint_replay,
    evaluate_cost_latency_budget,
    evaluate_event_schema,
    evaluate_terminal_state,
    evaluate_tool_policy,
)
from agent_driver.evals.persisted_replay import (
    graph_profile_tool_summary,
    replay_from_persisted,
)
from agent_driver.evals.replay import (
    build_support_bundle,
    render_cli_replay,
    render_full_debug_view,
    render_succinct_view,
)
from agent_driver.evals.runner import run_dataset

__all__ = [
    "BudgetLimits",
    "CaseEvaluation",
    "DatasetCase",
    "EvalReport",
    "EvaluatorResult",
    "ReportComparison",
    "build_support_bundle",
    "compare_reports",
    "default_evaluators",
    "evaluate_checkpoint_replay",
    "evaluate_cost_latency_budget",
    "evaluate_event_schema",
    "evaluate_terminal_state",
    "evaluate_tool_policy",
    "render_cli_replay",
    "render_full_debug_view",
    "render_succinct_view",
    "replay_from_persisted",
    "graph_profile_tool_summary",
    "run_dataset",
]
