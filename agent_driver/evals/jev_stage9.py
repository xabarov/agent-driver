"""Active compaction and memory evidence for the JEV rollout.

Stage 8 proved the label ledger and the low-risk promotion path.  This stage
turns the two deliberately held gates into reviewed, active evidence.  The
adapter consumes only already-produced benchmark receipts and writes bounded
labels; prompts, answers, transcript text, and provider request bodies never
enter the stage report or the label ledger.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path
from statistics import mean
from typing import Any

from agent_driver.evals.jev_stage8 import PromotionSettings, evaluate_production_promotion
from agent_driver.llm.rollout import JevOutcomeLabel, JevProductionLabelLedger

SCHEMA = "jev-stage9-active-evidence.v1"
_COMPACTION_SCENARIO_EXCLUSIONS = {"no_pressure_control"}


def _sha(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


def _read(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return value


def _label_id(gate: str, decision_id: str) -> str:
    return f"stage9-{gate}-{_sha(decision_id)[:24]}"


def _memory_labels(report: dict[str, Any], *, source: str) -> list[JevOutcomeLabel]:
    labels: list[JevOutcomeLabel] = []
    for row in report.get("rows", []):
        if row.get("surface") != "memory":
            continue
        active = row.get("treatments", [None, None, None])[2]
        call = row.get("call", {})
        if not isinstance(active, dict) or not isinstance(call, dict):
            continue
        decision_id = str(call.get("request_id") or _sha({"memory": row.get("repeat")}))
        invariants = active.get("invariants", {})
        labels.append(JevOutcomeLabel(
            label_id=_label_id("memory", decision_id),
            decision_id=decision_id,
            gate="memory",
            task="memory_durability",
            correct=active.get("decision_correct") is True,
            safety_passed=bool(invariants) and all(invariants.values()),
            fallback=bool(active.get("fallback")),
            latency_ms=call.get("latency_ms"),
            cost_usd=call.get("cost_usd"),
            source=source,
            window_id=f"w{int(row.get('repeat', 0)) + 1}",
        ))
    return labels


def _compaction_labels(report: dict[str, Any], *, source: str) -> list[JevOutcomeLabel]:
    labels: list[JevOutcomeLabel] = []
    for row in report.get("rows", []):
        if row.get("mode") != "jev" or row.get("scenario") in _COMPACTION_SCENARIO_EXCLUSIONS:
            continue
        responses = row.get("decision_responses", [])
        response = responses[0] if responses else {}
        if not isinstance(response, dict):
            response = {}
        decision_id = str(response.get("request_id") or _sha({
            "scenario": row.get("scenario"), "repeat": row.get("repeat"),
        }))
        fallback = bool(row.get("error") or row.get("prepass", {}).get("fallback_reason"))
        exact = row.get("answer_exact_match") is True
        protected = row.get("protected_fact_recall") in (None, 1, 1.0)
        safety = (
            row.get("error") is None
            and row.get("compaction_success") is True
            and row.get("durable_transcript_unchanged") is True
            and row.get("cost_complete") is True
        )
        labels.append(JevOutcomeLabel(
            label_id=_label_id("compaction", decision_id),
            decision_id=decision_id,
            gate="compaction",
            task="context_compaction",
            correct=exact and protected and not fallback,
            safety_passed=safety,
            fallback=fallback,
            latency_ms=response.get("latency_ms"),
            cost_usd=response.get("cost_usd"),
            source=source,
            window_id=f"w{int(row.get('repeat', 0)) + 1}",
        ))
    return labels


def build_active_evidence_labels(
    compaction_report: dict[str, Any],
    memory_report: dict[str, Any],
    *,
    source: str = "production_review",
) -> list[JevOutcomeLabel]:
    """Convert active live receipts into bounded reviewed labels."""
    if compaction_report.get("live") is not True or memory_report.get("live") is not True:
        raise ValueError("Stage 9 requires live compaction and memory reports")
    return _compaction_labels(compaction_report, source=source) + _memory_labels(memory_report, source=source)


def _evidence_summary(labels: list[JevOutcomeLabel]) -> dict[str, Any]:
    summary: dict[str, Any] = {}
    for gate in ("compaction", "memory"):
        selected = [item for item in labels if item.gate == gate]
        latencies = [float(item.latency_ms) for item in selected if item.latency_ms is not None]
        costs = [float(item.cost_usd) for item in selected if item.cost_usd is not None]
        windows = sorted({item.window_id for item in selected})
        summary[gate] = {
            "labels": len(selected),
            "windows": windows,
            "accuracy": mean(item.correct for item in selected) if selected else None,
            "fallback_rate": mean(item.fallback for item in selected) if selected else None,
            "safety_passed": bool(selected) and all(item.safety_passed for item in selected),
            "latency_p95_ms": sorted(latencies)[max(0, int(len(latencies) * 0.95 + 0.999) - 1)] if latencies else None,
            "mean_cost_per_decision_usd": mean(costs) if costs else None,
        }
    return summary


def evaluate_stage9(
    *,
    compaction_report: dict[str, Any],
    memory_report: dict[str, Any],
    routing_quality_labels: list[JevOutcomeLabel],
    stage7_report: dict[str, Any] | None = None,
    source: str = "production_review",
    operator_approved_default_on: bool = False,
) -> tuple[dict[str, Any], list[JevOutcomeLabel]]:
    """Evaluate held gates using a gate-specific calibrated evidence profile."""
    active = build_active_evidence_labels(compaction_report, memory_report, source=source)
    labels = list(routing_quality_labels) + active
    # Compaction batches several decisions and consequently have a higher
    # per-decision cost and latency budget than routing/quality.  The limits
    # are explicit here rather than silently reusing Stage 8's low-risk limit.
    promotion = evaluate_production_promotion(
        labels,
        stage7_report=stage7_report,
        settings=PromotionSettings(
            max_latency_p95_ms=30_000.0,
            max_cost_per_decision_usd=0.0004,
            operator_approved_default_on=operator_approved_default_on,
        ),
    )
    report = {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "label_source": source,
        "input_reports": {
            "compaction": {"schema": compaction_report.get("schema"), "sha256": _sha(compaction_report)},
            "memory": {"schema": memory_report.get("schema"), "sha256": _sha(memory_report)},
        },
        "evidence": _evidence_summary(active),
        "labels": {
            "routing_quality": len(routing_quality_labels),
            "active": len(active),
            "total": len(labels),
            "gates": dict(Counter(item.gate for item in labels)),
        },
        "promotion": promotion,
        "policy": {
            "compaction_and_memory_are_active_evidence_only": True,
            "default_on_requires_operator_approval": True,
            "runtime_default_on": False,
        },
    }
    return report, active


def render_report(report: dict[str, Any]) -> str:
    promotion = report["promotion"]["promotion"]
    lines = [
        "# JEV Stage 9 active compaction and memory evidence", "",
        f"Run: {report['created_at']}; source: `{report['label_source']}`.",
        "The active evidence is reviewed preproduction evidence; it does not claim end-user production traffic.",
        "", "| Gate | Labels | Windows | Accuracy | Fallback | p95 ms | Mean cost | Safety |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for gate, item in report["evidence"].items():
        lines.append(
            f"| {gate} | {item['labels']} | {len(item['windows'])} | {item['accuracy']} | "
            f"{item['fallback_rate']} | {item['latency_p95_ms']} | "
            f"{item['mean_cost_per_decision_usd']} | {item['safety_passed']} |"
        )
    lines.extend([
        "", f"Promotion policy stage: **`{promotion['stage']}`** (`{promotion['recommendation']}`).",
        "Active runtime gates: " + (", ".join(promotion["active_gates"]) or "none") + ".",
        "Held runtime gates: " + (", ".join(promotion["held_gates"]) or "none") + ".",
        "Default-on enabled: " + str(promotion["default_on_enabled"]) + "; operator approval required: " + str(promotion["operator_approval_required"]) + ".",
        "", "Reports and labels contain only bounded outcomes, costs, timing, and input hashes; no prompts, answers, or credentials.", "",
    ])
    return "\n".join(lines)


def write_artifacts(report: dict[str, Any], labels: list[JevOutcomeLabel], output: Path) -> tuple[Path, Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path, labels_path = output / "report.json", output / "report.md", output / "active-evidence-labels.jsonl"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_report(report), encoding="utf-8")
    ledger = JevProductionLabelLedger(labels_path)
    for label in labels:
        ledger.append(label)
    return json_path, markdown_path, labels_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compaction-report", type=Path, required=True)
    parser.add_argument("--memory-report", type=Path, required=True)
    parser.add_argument("--routing-quality-labels", type=Path, required=True)
    parser.add_argument("--stage7-report", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-stage9/active-evidence"))
    parser.add_argument("--operator-approved-default-on", action="store_true")
    args = parser.parse_args()
    routing_quality = JevProductionLabelLedger(args.routing_quality_labels).read()
    report, active = evaluate_stage9(
        compaction_report=_read(args.compaction_report),
        memory_report=_read(args.memory_report),
        routing_quality_labels=routing_quality,
        stage7_report=_read(args.stage7_report),
        operator_approved_default_on=args.operator_approved_default_on,
    )
    paths = write_artifacts(report, active, args.output)
    print(f"Report: {paths[1]}; stage={report['promotion']['promotion']['stage']}")


if __name__ == "__main__":
    main()


__all__ = [
    "SCHEMA",
    "build_active_evidence_labels",
    "evaluate_stage9",
    "render_report",
    "write_artifacts",
]
