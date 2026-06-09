"""Deterministic evaluation harness, dataset runners, and baseline reports."""

from agent_driver.evals.aggregate import (
    MetricSummary,
    RunAggregate,
    aggregate_trajectories,
    percentile,
)
from agent_driver.evals.baseline import compare_reports
from agent_driver.evals.compare import (
    ComparisonReport,
    compare_aggregates,
    render_comparison,
    run_comparison,
)
from agent_driver.evals.context_quality import (
    ContextQualityFixture,
    build_synthetic_context_quality_fixture,
    compaction_default_gate,
    evaluate_fixture_retention,
    evaluate_baseline_strategies,
    score_context_quality,
)
from agent_driver.evals.context_compaction_runner import (
    ContextQualityGatePolicy,
    ContextQualityGateResult,
    StrategyComparisonRow,
    render_context_compaction_gate_report,
    render_context_compaction_report,
    run_context_compaction_regression_gate,
    run_context_compaction_strategy_comparison,
)
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
    "ComparisonReport",
    "MetricSummary",
    "ReportComparison",
    "RunAggregate",
    "aggregate_trajectories",
    "compare_aggregates",
    "render_comparison",
    "run_comparison",
    "percentile",
    "build_support_bundle",
    "ContextQualityFixture",
    "build_synthetic_context_quality_fixture",
    "compaction_default_gate",
    "compare_reports",
    "default_evaluators",
    "evaluate_checkpoint_replay",
    "evaluate_cost_latency_budget",
    "evaluate_event_schema",
    "evaluate_terminal_state",
    "evaluate_tool_policy",
    "render_cli_replay",
    "render_context_compaction_gate_report",
    "render_context_compaction_report",
    "render_full_debug_view",
    "render_succinct_view",
    "replay_from_persisted",
    "graph_profile_tool_summary",
    "evaluate_fixture_retention",
    "evaluate_baseline_strategies",
    "run_dataset",
    "run_context_compaction_strategy_comparison",
    "run_context_compaction_regression_gate",
    "score_context_quality",
    "StrategyComparisonRow",
    "ContextQualityGatePolicy",
    "ContextQualityGateResult",
]
