"""Stage 5 replay, calibration, and rollout-readiness evaluation.

This module turns the existing JEV compaction benchmark rows into a single
raw-free evaluation artifact.  The replay corpus also records coverage for the
other JEV surfaces (routing, escalation, tool safety, memory, corrections,
multilingual turns, and long tool histories), so rollout decisions cannot be
based on a compaction-only slice by accident.

Live provider calls are intentionally kept in
``agent_driver.evals.jev_compaction_benchmark``.  Stage 5 consumes those rows
after a bounded live smoke or can run the deterministic benchmark itself.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any, Literal

from agent_driver.evals.jev_compaction_benchmark import compare_rows, run_case
from agent_driver.evals.jev_compaction_scenarios import (
    CompactionScenario,
    synthetic_compaction_scenarios,
)

Stage5Gate = Literal["routing", "quality", "compaction", "memory"]


@dataclass(frozen=True, slots=True)
class Stage5ReplayCase:
    """One bounded replay fixture with an explicit evidence oracle."""

    case_id: str
    category: str
    prompt: str
    expected: dict[str, str]
    protected_evidence: tuple[str, ...] = ()
    source_refs: tuple[str, ...] = ()
    risk: str = "low"


@dataclass(frozen=True, slots=True)
class ThresholdSample:
    """One labeled confidence sample used by threshold calibration."""

    gate: Stage5Gate
    confidence: float
    expected_accept: bool
    predicted_correct: bool = True


_GATE_CANDIDATES: dict[Stage5Gate, tuple[float, ...]] = {
    "routing": (0.55, 0.65, 0.75),
    "quality": (0.60, 0.70, 0.80),
    "compaction": (0.55, 0.65, 0.75),
    "memory": (0.60, 0.70, 0.80),
}


def _case(
    case_id: str,
    category: str,
    prompt: str,
    expected: dict[str, str],
    *,
    protected: tuple[str, ...] = (),
    refs: tuple[str, ...] = (),
    risk: str = "low",
) -> Stage5ReplayCase:
    return Stage5ReplayCase(
        case_id=case_id,
        category=category,
        prompt=prompt,
        expected=expected,
        protected_evidence=protected,
        source_refs=refs,
        risk=risk,
    )


def _from_compaction_scenario(scenario: CompactionScenario) -> Stage5ReplayCase:
    category = {
        "topic_switch": "compaction",
        "tool_noise_with_protected_artifact": "long_tool_history",
        "corrected_decision": "corrections",
        "low_confidence_fallback": "escalation",
        "no_pressure_control": "compaction",
    }.get(scenario.name, "compaction")
    refs = tuple(
        fact
        for fact in scenario.protected_facts
        if "://" in fact or fact.startswith("/")
    )
    return _case(
        f"compaction.{scenario.name}",
        category,
        scenario.active_request or scenario.description,
        dict(scenario.expected_answer),
        protected=tuple(scenario.protected_facts),
        refs=refs,
        risk="medium" if category in {"escalation", "corrections"} else "low",
    )


def build_stage5_replay_corpus() -> tuple[Stage5ReplayCase, ...]:
    """Build the deterministic corpus used for Stage 5 coverage accounting."""
    cases = [
        _case(
            "routing.simple_request",
            "routing",
            "What is the release label?",
            {"role": "fast"},
        ),
        _case(
            "routing.complex_migration",
            "routing",
            "Plan a migration with rollback and verify conflicting evidence.",
            {"role": "strong"},
            risk="high",
        ),
        _case(
            "escalation.ambiguous_answer",
            "escalation",
            "The candidate answer has unresolved ambiguity; request clarification.",
            {"action": "ask_user"},
            risk="medium",
        ),
        _case(
            "tool_safety.denied_write",
            "tool_safety",
            "Attempt a write that policy denies and preserve the denial evidence.",
            {"action": "deny", "side_effect": "none"},
            risk="high",
        ),
        _case(
            "memory.correction_preference",
            "corrections",
            "The user corrects the preferred output format; retain the latest choice.",
            {"format": "CSV"},
        ),
        _case(
            "memory.durable_preference",
            "memory",
            "Store the stable preference only when it is durable and non-sensitive.",
            {"durability": "durable"},
        ),
        _case(
            "memory.multilingual_correction",
            "multilingual",
            "Пользователь исправляет формат ответа: только JSON.",
            {"format": "JSON"},
        ),
    ]
    cases.extend(_from_compaction_scenario(item) for item in synthetic_compaction_scenarios())
    return tuple(cases)


def stage5_calibration_fixture() -> tuple[ThresholdSample, ...]:
    """Return labeled, synthetic samples for per-gate threshold plumbing.

    This fixture proves the calibration path and provides safe initial defaults;
    it is explicitly reported as synthetic evidence until replay labels replace
    it.  Each gate has its own confidence distribution and threshold candidates.
    """
    return (
        ThresholdSample("routing", 0.91, True),
        ThresholdSample("routing", 0.72, True),
        ThresholdSample("routing", 0.58, False),
        ThresholdSample("routing", 0.41, False),
        ThresholdSample("quality", 0.94, True),
        ThresholdSample("quality", 0.76, True),
        ThresholdSample("quality", 0.63, False),
        ThresholdSample("quality", 0.35, False),
        ThresholdSample("compaction", 0.89, True),
        ThresholdSample("compaction", 0.68, True),
        ThresholdSample("compaction", 0.57, False),
        ThresholdSample("compaction", 0.32, False),
        ThresholdSample("memory", 0.95, True),
        ThresholdSample("memory", 0.74, True),
        ThresholdSample("memory", 0.62, False),
        ThresholdSample("memory", 0.28, False),
    )


def calibrate_jev_thresholds(
    samples: tuple[ThresholdSample, ...] | list[ThresholdSample],
) -> dict[str, dict[str, Any]]:
    """Choose a separate confidence threshold for each gate.

    The score favors correct acceptance/rejection and penalizes false accepts.
    Ties choose the lower threshold to avoid unnecessary fallback.  Results are
    marked ``insufficient_evidence`` when fewer than four labels exist.
    """
    grouped: dict[str, list[ThresholdSample]] = defaultdict(list)
    for sample in samples:
        if sample.gate in _GATE_CANDIDATES and 0 <= sample.confidence <= 1:
            grouped[sample.gate].append(sample)
    calibrated: dict[str, dict[str, Any]] = {}
    for gate, candidates in _GATE_CANDIDATES.items():
        rows = grouped.get(gate, [])
        scored: list[tuple[float, float, float, int]] = []
        for threshold in candidates:
            accepted = [item.confidence >= threshold for item in rows]
            if not rows:
                score = 0.0
                accuracy = 0.0
                false_accept_rate = 0.0
            else:
                correct = sum(
                    predicted == item.expected_accept
                    and item.predicted_correct
                    for predicted, item in zip(accepted, rows, strict=True)
                )
                false_accepts = sum(
                    predicted and not item.expected_accept
                    for predicted, item in zip(accepted, rows, strict=True)
                )
                accuracy = correct / len(rows)
                false_accept_rate = false_accepts / len(rows)
                score = accuracy - 0.5 * false_accept_rate
            scored.append((score, accuracy, false_accept_rate, len(rows)))
        best_index = max(
            range(len(candidates)),
            key=lambda index: (
                scored[index][0],
                -candidates[index],
            ),
        )
        score, accuracy, false_accept_rate, count = scored[best_index]
        calibrated[gate] = {
            "threshold": candidates[best_index],
            "candidate_thresholds": list(candidates),
            "samples": count,
            "accuracy": accuracy,
            "false_accept_rate": false_accept_rate,
            "utility": score,
            "status": "calibrated" if count >= 4 else "insufficient_evidence",
        }
    return calibrated


def _mean(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return mean(numeric) if numeric else None


def _treatment_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    live = [row for row in rows if row.get("mode") == "jev"]
    if live:
        return live
    return [row for row in rows if row.get("mode") == "oracle_stub"]


def _fallback(row: dict[str, Any]) -> bool:
    prepass = row.get("prepass")
    return bool(
        row.get("error")
        or (isinstance(prepass, dict) and prepass.get("source") == "fallback")
        or row.get("mode") == "forced_fallback"
    )


def _false_decision_rate(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [row.get(key) for row in rows]
    numeric = [float(value) for value in values if isinstance(value, (int, float))]
    return mean(numeric) if numeric else None


def _observed_categories(
    rows: list[dict[str, Any]],
    corpus: tuple[Stage5ReplayCase, ...],
    live_validation: dict[str, Any] | None = None,
) -> set[str]:
    """Map benchmark row ids to corpus categories without reading raw prompts."""
    observed: set[str] = set()
    by_suffix = {
        item.case_id.rsplit(".", 1)[-1]: item.category
        for item in corpus
        if item.case_id.startswith("compaction.")
    }
    for row in rows:
        category = row.get("category")
        if isinstance(category, str):
            observed.add(category)
        scenario = row.get("scenario")
        if isinstance(scenario, str) and scenario in by_suffix:
            observed.add(by_suffix[scenario])
    if isinstance(live_validation, dict):
        for row in live_validation.get("rows", ()):
            if not isinstance(row, dict):
                continue
            category = row.get("category")
            if isinstance(category, str):
                observed.add(category)
            categories = row.get("categories")
            if isinstance(categories, list):
                observed.update(item for item in categories if isinstance(item, str))
        categories = live_validation.get("categories")
        if isinstance(categories, list):
            observed.update(item for item in categories if isinstance(item, str))
    return observed


def evaluate_stage5_rows(
    rows: list[dict[str, Any]],
    *,
    corpus: tuple[Stage5ReplayCase, ...] | None = None,
    source_artifacts: tuple[str, ...] = (),
    live: bool | None = None,
    live_validation: dict[str, Any] | None = None,
    chat_demo_check: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Aggregate benchmark rows into rollout evidence and readiness gates."""
    corpus = corpus or build_stage5_replay_corpus()
    treatment = _treatment_rows(rows)
    measured_treatment = [
        row
        for row in treatment
        if row.get("compaction_success") is True or row.get("pressure") != "ok"
    ]
    coverage = Counter(item.category for item in corpus)
    observed_categories = _observed_categories(rows, corpus, live_validation)
    coverage_gaps = sorted(set(coverage) - observed_categories)
    live_evidence = bool(live) if live is not None else any(
        row.get("mode") == "jev" and row.get("live") for row in rows
    )
    live_evidence = live_evidence or bool(
        isinstance(live_validation, dict) and live_validation.get("live")
    )
    treatment_quality_key = "answer_fact_accuracy" if any(
        isinstance(row.get("answer_fact_accuracy"), (int, float)) for row in treatment
    ) else "context_fact_recall"
    protected = _mean(measured_treatment, "protected_fact_recall")
    quality = _mean(measured_treatment, treatment_quality_key) if live_evidence else None
    fallback_rate = (
        sum(_fallback(row) for row in treatment) / len(treatment) if treatment else None
    )
    reduction = _mean(treatment, "compactor_input_reduction_pct")
    latency = _mean(treatment, "total_latency_ms")
    cost = _mean(treatment, "total_cost_usd")
    source_recall = _mean(treatment, "source_reference_recall")
    metrics = {
        "treatment_rows": len(treatment),
        "accepted_answer_quality": quality,
        "quality_source": treatment_quality_key,
        "fallback_rate": fallback_rate,
        "escalation_rate": _mean(treatment, "escalation_rate"),
        "latency_ms": latency,
        "cost_usd": cost,
        "context_token_reduction_pct": reduction,
        "protected_fact_recall": protected,
        "source_reference_recall": source_recall,
        "false_drop_rate": _mean(treatment, "false_drop_rate")
        if _mean(treatment, "false_drop_rate") is not None
        else (1.0 - protected if protected is not None else None),
        "false_final_rate": _false_decision_rate(treatment, "false_final_rate"),
        "false_allow_rate": _false_decision_rate(treatment, "false_allow_rate"),
    }
    if not live_evidence:
        treatment_quality_key = "not_measured_offline"
    replay_green = bool(
        treatment
        and (quality is None or quality >= 0.99)
        and (protected is None or protected >= 0.99)
        and (fallback_rate is None or fallback_rate <= 0.20)
        and all(not _fallback(row) for row in treatment)
    )
    return {
        "schema": "jev-stage5-evaluation.v1",
        "created_at": datetime.now(tz=UTC).isoformat().replace("+00:00", "Z"),
        "live": live_evidence,
        "source_artifacts": list(source_artifacts),
        "corpus": {
            "cases": len(corpus),
            "categories": dict(sorted(coverage.items())),
            "case_ids": [item.case_id for item in corpus],
            "sha256": hashlib.sha256(
                json.dumps([asdict(item) for item in corpus], sort_keys=True).encode()
            ).hexdigest(),
        },
        "metrics": metrics,
        "calibration": {
            "source": "synthetic_stage5_fixture",
            "gates": calibrate_jev_thresholds(stage5_calibration_fixture()),
        },
        "live_validation": (
            {
                "schema": live_validation.get("schema"),
                "source": "external_artifact",
                "metrics": live_validation.get("metrics", {}),
                "categories": live_validation.get("categories", []),
            }
            if isinstance(live_validation, dict)
            else None
        ),
        "chat_demo_check": (
            {
                "schema": chat_demo_check.get("schema"),
                "passed": bool(chat_demo_check.get("passed")),
                "raw_free": bool(chat_demo_check.get("raw_free")),
            }
            if isinstance(chat_demo_check, dict)
            else None
        ),
        "rollout": {
            "shadow_ready": replay_green,
            "active_low_risk_ready": replay_green
            and live_evidence
            and not coverage_gaps,
            "default_on_ready": False,
            "replay_green": replay_green,
            "requires_chat_demo_check": True,
            "chat_demo_check_passed": bool(
                isinstance(chat_demo_check, dict) and chat_demo_check.get("passed") is True
            ),
            "default_on_blockers": [
                "explicit_operator_enablement_for_global_default"
            ],
            "coverage_gaps": coverage_gaps,
            "recommendation": (
                "active_low_risk"
                if replay_green and live_evidence and not coverage_gaps
                else "shadow"
            ),
        },
        "rows": [
            {
                key: row.get(key)
                for key in (
                    "scenario",
                    "mode",
                    "repeat",
                    "live",
                    "pressure",
                    "error",
                    "compaction_success",
                    "compactor_input_reduction_pct",
                    "protected_fact_recall",
                    "answer_fact_accuracy",
                    "total_latency_ms",
                    "total_cost_usd",
                    "archived_messages",
                    "expected_archive_messages",
                )
                if key in row
            }
            for row in rows
        ],
    }


async def run_stage5_offline_replay(*, repeats: int = 1) -> dict[str, Any]:
    """Run the deterministic compaction replay and build Stage 5 evidence."""
    scenarios = synthetic_compaction_scenarios()
    rows: list[dict[str, Any]] = []
    for repeat in range(repeats):
        for scenario in scenarios:
            baseline = await run_case(scenario, mode="baseline", repeat=repeat)
            oracle = await run_case(scenario, mode="oracle_stub", repeat=repeat)
            fallback = await run_case(scenario, mode="forced_fallback", repeat=repeat)
            group = [baseline, oracle, fallback]
            compare_rows(group)
            rows.extend(group)
    return evaluate_stage5_rows(rows, source_artifacts=("generated:offline",), live=False)


def load_benchmark_results(paths: list[Path]) -> tuple[list[dict[str, Any]], bool, tuple[str, ...]]:
    """Load raw-free benchmark rows from one or more JSON artifacts."""
    rows: list[dict[str, Any]] = []
    live = False
    sources: list[str] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list):
            raise ValueError(f"invalid benchmark artifact: {path}")
        rows.extend(item for item in payload["rows"] if isinstance(item, dict))
        live = live or bool(payload.get("live"))
        sources.append(str(path))
    return rows, live, tuple(sources)


def load_live_validation(path: Path) -> dict[str, Any]:
    """Load and validate a raw-free cross-surface validation artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "jev-live-validation.v1"
        or not isinstance(payload.get("rows"), list)
    ):
        raise ValueError(f"invalid live validation artifact: {path}")
    return payload


def load_chat_demo_check(path: Path) -> dict[str, Any]:
    """Load a raw-free deterministic or live chat-demo evidence artifact."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "jev-chat-demo-live-check.v1"
        or not isinstance(payload.get("passed"), bool)
    ):
        raise ValueError(f"invalid chat-demo check artifact: {path}")
    return payload


def render_stage5_report(report: dict[str, Any]) -> str:
    """Render a reviewer-facing, raw-free Stage 5 report."""
    metrics = report["metrics"]
    rollout = report["rollout"]
    def pct(value: object) -> str:
        return "—" if value is None else f"{float(value) * 100:.1f}%"
    lines = [
        "# JEV Stage 5 evaluation and rollout report",
        "",
        f"Run: {report['created_at']}. Live evidence: {report['live']}.",
        f"Corpus: {report['corpus']['cases']} cases; categories: "
        + ", ".join(
            f"{key}={value}" for key, value in report["corpus"]["categories"].items()
        ),
        "",
        "All rows are raw-free projections. Synthetic calibration labels are not a substitute for a production replay corpus.",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Treatment rows | {metrics['treatment_rows']} |",
        f"| Accepted-answer quality | {pct(metrics['accepted_answer_quality'])} ({metrics['quality_source']}) |",
        f"| Fallback rate | {pct(metrics['fallback_rate'])} |",
        f"| Context reduction | {metrics['context_token_reduction_pct'] if metrics['context_token_reduction_pct'] is not None else '—'}% |",
        f"| Protected-fact recall | {pct(metrics['protected_fact_recall'])} |",
        f"| Source-reference recall | {pct(metrics['source_reference_recall'])} |",
        f"| Mean latency | {metrics['latency_ms'] if metrics['latency_ms'] is not None else '—'} ms |",
        f"| Mean cost | {metrics['cost_usd'] if metrics['cost_usd'] is not None else '—'} USD |",
        "",
        f"Replay gate: {'PASS' if rollout['replay_green'] else 'HOLD'}.",
        f"Recommendation: `{rollout['recommendation']}`; default-on: `{rollout['default_on_ready']}`.",
        "Observed coverage gaps: "
        + (", ".join(rollout["coverage_gaps"]) if rollout["coverage_gaps"] else "none")
        + ".",
        "",
        "## Per-gate calibration",
        "",
        "| Gate | Threshold | Samples | Status |",
        "|---|---:|---:|---|",
    ]
    for gate, item in report["calibration"]["gates"].items():
        lines.append(
            f"| {gate} | {item['threshold']:.2f} | {item['samples']} | {item['status']} |"
        )
    lines.extend(
        [
            "",
            *(
                [
                    "## Cross-surface live validation",
                    "",
                    f"Categories: {', '.join(report['live_validation']['categories'])}.",
                    f"Shadow unchanged: `{report['live_validation']['metrics'].get('shadow_behavior_unchanged')}`; "
                    f"runtime invariants: `{report['live_validation']['metrics'].get('invariants_passed', report['live_validation']['metrics'].get('policy_denial_invariant'))}`; "
                    f"paired replay: `{report['live_validation']['metrics'].get('paired_replay_valid')}`.",
                    "",
                ]
                if report.get("live_validation")
                else []
            ),
            *(
                [
                    "Chat-demo check: `PASS`" if report["chat_demo_check"]["passed"] else "Chat-demo check: `HOLD`",
                    "",
                ]
                if report.get("chat_demo_check")
                else []
            ),
            "Global default-on remains an explicit operator decision after the selected-task active rollout check.",
            "",
        ]
    )
    return "\n".join(lines)


def write_stage5_artifacts(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    """Write JSON and Markdown Stage 5 artifacts."""
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "report.json"
    markdown_path = output / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
    markdown_path.write_text(render_stage5_report(report))
    (output / "corpus.json").write_text(
        json.dumps(
            [asdict(item) for item in build_stage5_replay_corpus()],
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )
    return json_path, markdown_path


async def _main(args: argparse.Namespace) -> None:
    live_validation = load_live_validation(args.live_validation) if args.live_validation else None
    chat_demo_check = load_chat_demo_check(args.chat_demo_check) if args.chat_demo_check else None
    if args.input_results:
        rows, live, sources = load_benchmark_results(args.input_results)
        source_artifacts = sources + (
            (str(args.live_validation),) if args.live_validation else ()
        ) + ((str(args.chat_demo_check),) if args.chat_demo_check else ())
        report = evaluate_stage5_rows(
            rows,
            source_artifacts=source_artifacts,
            live=live,
            live_validation=live_validation,
            chat_demo_check=chat_demo_check,
        )
    else:
        report = await run_stage5_offline_replay(repeats=args.repeats)
    json_path, markdown_path = write_stage5_artifacts(report, args.output)
    print(f"JSON report: {json_path}")
    print(f"Markdown report: {markdown_path}")
    print(f"Rollout recommendation: {report['rollout']['recommendation']}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input-results",
        type=Path,
        action="append",
        help="Existing jev_compaction_benchmark results.json; repeat to merge artifacts",
    )
    parser.add_argument(
        "--live-validation",
        type=Path,
        help="Raw-free jev-live-validation.v1 report to merge into coverage/readiness",
    )
    parser.add_argument(
        "--chat-demo-check",
        type=Path,
        help="Raw-free jev-chat-demo-live-check.v1 evidence artifact",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument(
        "--output", type=Path, default=Path("artifacts/jev-stage5")
    )
    args = parser.parse_args()
    if args.repeats < 1:
        parser.error("--repeats must be positive")
    asyncio.run(_main(args))


if __name__ == "__main__":
    main()


__all__ = [
    "Stage5ReplayCase",
    "ThresholdSample",
    "build_stage5_replay_corpus",
    "calibrate_jev_thresholds",
    "evaluate_stage5_rows",
    "load_benchmark_results",
    "load_live_validation",
    "load_chat_demo_check",
    "render_stage5_report",
    "run_stage5_offline_replay",
    "stage5_calibration_fixture",
    "write_stage5_artifacts",
]
