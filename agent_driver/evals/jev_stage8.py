"""Production label ingestion and gradual JEV promotion policy."""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime
from math import ceil, isfinite
from pathlib import Path
from statistics import mean
from typing import Any

from agent_driver.llm.rollout import (
    JevOutcomeLabel,
    JevProductionLabelLedger,
    unique_outcome_labels,
)

SCHEMA = "jev-stage8-production-promotion.v1"
_GATES = ("routing", "quality", "compaction", "memory")
_LOW_RISK_GATES = ("routing", "quality")


@dataclass(frozen=True, slots=True)
class PromotionSettings:
    """Production evidence gates; no threshold enables default-on by itself."""

    min_labels_per_gate: int = 5
    min_windows: int = 2
    min_accuracy: float = 0.98
    max_fallback_rate: float = 0.02
    max_latency_p95_ms: float = 5000.0
    max_cost_per_decision_usd: float = 0.0001
    operator_approved_default_on: bool = False

    def __post_init__(self) -> None:
        if self.min_labels_per_gate < 1 or self.min_windows < 1:
            raise ValueError("minimum label and window counts must be positive")
        if not 0 <= self.min_accuracy <= 1 or not 0 <= self.max_fallback_rate <= 1:
            raise ValueError("accuracy and fallback limits must be between 0 and 1")
        if any(not isfinite(value) or value < 0 for value in (
            self.max_latency_p95_ms, self.max_cost_per_decision_usd,
        )):
            raise ValueError("latency and cost limits must be finite and non-negative")


def _p95(values: list[float]) -> float | None:
    return sorted(values)[max(0, ceil(len(values) * 0.95) - 1)] if values else None


def evaluate_gate_evidence(labels: list[JevOutcomeLabel], settings: PromotionSettings) -> dict[str, Any]:
    """Measure a gate without changing the provenance of its observations."""
    labels = unique_outcome_labels(labels)
    latencies = [float(item.latency_ms) for item in labels if item.latency_ms is not None]
    costs = [float(item.cost_usd) for item in labels if item.cost_usd is not None]
    accuracy = mean(item.correct for item in labels) if labels else None
    fallback = mean(item.fallback for item in labels) if labels else None
    safety = all(item.safety_passed for item in labels) if labels else False
    windows = sorted({item.window_id for item in labels})
    checks = {
        "sample_count": len(labels) >= settings.min_labels_per_gate,
        "window_count": len(windows) >= settings.min_windows,
        "accuracy": accuracy is not None and accuracy >= settings.min_accuracy,
        "fallback_rate": fallback is not None and fallback <= settings.max_fallback_rate,
        "safety": safety,
        "telemetry_complete": bool(labels) and len(latencies) == len(costs) == len(labels),
        "latency_p95": (
            _p95(latencies) is not None
            and _p95(latencies) <= settings.max_latency_p95_ms
        ),
        "cost": bool(costs) and mean(costs) <= settings.max_cost_per_decision_usd,
    }
    return {
        "labels": len(labels),
        "windows": windows,
        "accuracy": accuracy,
        "fallback_rate": fallback,
        "safety_passed": safety,
        "latency_p95_ms": _p95(latencies),
        "mean_cost_per_decision_usd": mean(costs) if costs else None,
        "checks": checks,
        "green": all(checks.values()),
    }


def evaluate_production_promotion(
    labels: list[JevOutcomeLabel],
    *,
    stage7_report: dict[str, Any] | None = None,
    settings: PromotionSettings | None = None,
) -> dict[str, Any]:
    """Return a safe, explicit promotion recommendation from reviewed labels."""
    settings = settings or PromotionSettings()
    production = [
        label for label in unique_outcome_labels(labels)
        if label.source in {"production", "production_review"}
    ]
    grouped: dict[str, list[JevOutcomeLabel]] = defaultdict(list)
    for label in production:
        grouped[label.gate].append(label)
    gates = {gate: evaluate_gate_evidence(grouped.get(gate, []), settings) for gate in _GATES}
    low_risk_green = all(gates[gate]["green"] for gate in _LOW_RISK_GATES)
    all_gates_green = all(gates[gate]["green"] for gate in _GATES)
    transport_green = stage7_report is not None and stage7_report.get("promotion", {}).get("passed") is True
    low_risk_ready = low_risk_green and transport_green
    normal_chat_ready = low_risk_ready and all(
        len(gates[gate]["windows"]) >= settings.min_windows for gate in _LOW_RISK_GATES
    )
    default_candidate = normal_chat_ready and all_gates_green
    if default_candidate and settings.operator_approved_default_on:
        stage, recommendation = "default_on", "enable_default_on"
    elif normal_chat_ready:
        stage, recommendation = "normal_chat_canary", "promote_normal_chat_canary"
    elif low_risk_ready:
        stage, recommendation = "low_risk_active", "promote_low_risk_active"
    else:
        stage, recommendation = "shadow", "collect_production_labels"
    reasons = []
    if not production:
        reasons.append("no_production_labels")
    if not transport_green:
        reasons.append("stage7_transport_not_approved")
    reasons.extend(
        f"{gate}_{check}"
        for gate, evidence in gates.items()
        for check, passed in evidence["checks"].items()
        if not passed and evidence["labels"] > 0
    )
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "label_source": "production_review" if production else "none",
        "metrics": {
            "input_labels": len(labels),
            "production_labels": len(production),
            "excluded_preproduction_labels": sum(label.source == "preproduction_review" for label in labels),
            "windows": sorted({label.window_id for label in production}),
            "gates_with_labels": sorted(grouped),
        },
        "gates": gates,
        "transport": {
            "stage7_passed": transport_green,
            "source": stage7_report.get("schema") if stage7_report else None,
        },
        "promotion": {
            "passed": stage != "shadow",
            "stage": stage,
            "recommendation": recommendation,
            "active_gates": (
                list(_GATES) if stage == "default_on" else list(_LOW_RISK_GATES)
            ) if stage != "shadow" else [],
            "held_gates": (
                [gate for gate in _GATES if gate not in _LOW_RISK_GATES]
                if stage != "default_on"
                else []
            ) if stage != "shadow" else list(_GATES),
            "operator_approval_required": default_candidate and not settings.operator_approved_default_on,
            "default_on_enabled": stage == "default_on",
            "rollback_required": any(
                not evidence["green"] and evidence["labels"] > 0
                for evidence in gates.values()
            ),
            "reasons": sorted(set(reasons)),
        },
        "settings": {
            "min_labels_per_gate": settings.min_labels_per_gate,
            "min_windows": settings.min_windows,
            "min_accuracy": settings.min_accuracy,
            "max_fallback_rate": settings.max_fallback_rate,
            "max_latency_p95_ms": settings.max_latency_p95_ms,
            "max_cost_per_decision_usd": settings.max_cost_per_decision_usd,
            "operator_approved_default_on": settings.operator_approved_default_on,
        },
    }


def load_labels(path: Path) -> list[JevOutcomeLabel]:
    return JevProductionLabelLedger(path).read()


def render_report(report: dict[str, Any]) -> str:
    promotion = report["promotion"]
    lines = [
        "# JEV Stage 8 production labels and promotion",
        "",
        f"Run: {report['created_at']}; label source: `{report['label_source']}`.",
        f"Promotion: **`{promotion['stage']}`** (`{promotion['recommendation']}`).",
        "",
        "| Gate | Labels | Windows | Accuracy | Fallback | p95 | Cost | Green |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ]
    for gate, item in report["gates"].items():
        lines.append(
            f"| {gate} | {item['labels']} | {len(item['windows'])} | {item['accuracy']} | "
            f"{item['fallback_rate']} | {item['latency_p95_ms']} | "
            f"{item['mean_cost_per_decision_usd']} | {item['green']} |"
        )
    lines.extend([
        "",
        "Stage 7 transport approved: " + str(report["transport"]["stage7_passed"]) + ".",
        "Active gates: " + (", ".join(promotion["active_gates"]) or "none") + ".",
        "Held gates: " + (", ".join(promotion["held_gates"]) or "none") + ".",
        "Reasons: " + (", ".join(promotion["reasons"]) or "none") + ".",
        "",
        "Labels contain no prompts, answers, transcript text, or credentials. Default-on always requires explicit operator approval.",
        "",
    ])
    return "\n".join(lines)


def write_artifacts(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path, markdown_path = output / "report.json", output / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_report(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--stage7-report", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-stage8/promotion"))
    parser.add_argument("--operator-approved-default-on", action="store_true")
    args = parser.parse_args()
    labels = load_labels(args.labels)
    stage7 = json.loads(args.stage7_report.read_text(encoding="utf-8")) if args.stage7_report else None
    report = evaluate_production_promotion(
        labels,
        stage7_report=stage7,
        settings=PromotionSettings(operator_approved_default_on=args.operator_approved_default_on),
    )
    paths = write_artifacts(report, args.output)
    print(f"Report: {paths[1]}; stage={report['promotion']['stage']}")


if __name__ == "__main__":
    main()


__all__ = [
    "PromotionSettings",
    "SCHEMA",
    "evaluate_production_promotion",
    "evaluate_gate_evidence",
    "load_labels",
    "render_report",
    "write_artifacts",
]
