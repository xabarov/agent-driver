"""Production observability and latency calibration for JEV canaries."""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA = "jev-stage7-latency-calibration.v1"


@dataclass(frozen=True, slots=True)
class LatencyCalibrationSettings:
    """Operator-owned latency and safety budgets."""

    target_p95_ms: float = 5000.0
    max_cost_per_decision_usd: float = 0.0001
    minimum_speedup: float = 0.05

    def __post_init__(self) -> None:
        if self.target_p95_ms < 0 or self.max_cost_per_decision_usd < 0:
            raise ValueError("latency and cost limits must be non-negative")
        if not 0 <= self.minimum_speedup <= 1:
            raise ValueError("minimum_speedup must be between 0 and 1")


def _num(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _phase_bottleneck(telemetry: dict[str, Any] | None) -> dict[str, Any] | None:
    phases = telemetry.get("phases") if isinstance(telemetry, dict) else None
    if not isinstance(phases, dict):
        return None
    candidates = [
        (str(name), _num(item.get("p95_ms")))
        for name, item in phases.items()
        if isinstance(item, dict) and _num(item.get("p95_ms")) is not None
    ]
    if not candidates:
        return None
    name, value = max(candidates, key=lambda item: item[1] or 0.0)
    return {"phase": name, "p95_ms": value}


def _variant(report: dict[str, Any]) -> dict[str, Any]:
    metrics = report.get("metrics", {})
    telemetry = report.get("transport_telemetry")
    latency = telemetry.get("latency", {}) if isinstance(telemetry, dict) else {}
    p95_latency = latency.get("p95_ms") if isinstance(latency, dict) else None
    return {
        "live": bool(report.get("live")),
        "passed": bool(report.get("passed")),
        "decision_calls": metrics.get("decision_calls"),
        "decision_accuracy": metrics.get("decision_accuracy"),
        "fallback_rate": metrics.get("fallback_rate"),
        "latency_p95_ms": p95_latency if isinstance(p95_latency, (int, float)) else metrics.get("latency_ms"),
        "mean_latency_ms": metrics.get("latency_ms"),
        "known_cost_usd": metrics.get("known_cost_usd"),
        "mean_cost_per_decision_usd": (
            (metrics.get("cost_usd") / metrics.get("decision_calls"))
            if isinstance(metrics.get("cost_usd"), (int, float))
            and isinstance(metrics.get("decision_calls"), int)
            and metrics.get("decision_calls")
            else None
        ),
        "telemetry": telemetry,
        "phase_bottleneck": _phase_bottleneck(telemetry),
    }


def evaluate_latency_calibration(
    baseline: dict[str, Any],
    pooled: dict[str, Any],
    *,
    settings: LatencyCalibrationSettings | None = None,
) -> dict[str, Any]:
    """Compare transport variants and select a safe runtime recommendation."""
    settings = settings or LatencyCalibrationSettings()
    base = _variant(baseline)
    candidate = _variant(pooled)
    base_p95 = _num(base["latency_p95_ms"])
    candidate_p95 = _num(candidate["latency_p95_ms"])
    speedup = (
        (base_p95 - candidate_p95) / base_p95
        if base_p95 and candidate_p95 is not None
        else None
    )
    checks = {
        # The baseline is a reference distribution. A transient baseline error
        # is retained as evidence but must not prevent a healthy candidate from
        # being selected when the candidate meets its own SLOs.
        "baseline_reference": base["live"]
        and isinstance(base["decision_calls"], int)
        and base["decision_calls"] > 0
        and isinstance(base["telemetry"], dict),
        "pooled_source": candidate["live"] and candidate["passed"],
        "pooled_accuracy": _num(candidate["decision_accuracy"]) is not None
        and float(candidate["decision_accuracy"]) >= 0.95,
        "pooled_no_fallback": _num(candidate["fallback_rate"]) == 0.0,
        "pooled_cost": _num(candidate["mean_cost_per_decision_usd"]) is not None
        and float(candidate["mean_cost_per_decision_usd"]) <= settings.max_cost_per_decision_usd,
        "target_p95": candidate_p95 is not None and candidate_p95 <= settings.target_p95_ms,
        "transport_improved": speedup is not None and speedup >= settings.minimum_speedup,
    }
    if checks["target_p95"]:
        recommendation = "promote_pooled_transport"
        status = "target_met"
    elif checks["transport_improved"]:
        recommendation = "continue_pooled_calibration"
        status = "improved_but_budget_not_met"
    else:
        recommendation = "hold_shadow_and_investigate"
        status = "no_safe_improvement"
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "settings": {
            "target_p95_ms": settings.target_p95_ms,
            "max_cost_per_decision_usd": settings.max_cost_per_decision_usd,
            "minimum_speedup": settings.minimum_speedup,
        },
        "baseline": base,
        "pooled": candidate,
        "comparison": {
            "p95_delta_ms": (candidate_p95 - base_p95)
            if candidate_p95 is not None and base_p95 is not None else None,
            "p95_speedup_fraction": speedup,
        },
        "checks": checks,
        "calibration": {
            "status": status,
            "recommended_transport": "pooled" if speedup is not None and speedup > 0 else "baseline",
            "phase_bottleneck": candidate["phase_bottleneck"],
            "production_labels_required": True,
        },
        "promotion": {
            "passed": all(checks.values()),
            "recommendation": recommendation,
            "rollback_required": not all(
                checks[name]
                for name in (
                    "pooled_source",
                    "pooled_accuracy",
                    "pooled_no_fallback",
                    "pooled_cost",
                    "target_p95",
                )
            ),
            "reasons": [name for name, passed in checks.items() if not passed],
        },
    }


def _load(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON artifact: {path}")
    return payload


def render_report(report: dict[str, Any]) -> str:
    comparison = report["comparison"]
    promotion = report["promotion"]
    lines = [
        "# JEV Stage 7 latency calibration",
        "",
        f"Run: {report['created_at']}; status: **{report['calibration']['status']}**.",
        f"Recommendation: **`{promotion['recommendation']}`**.",
        "",
        "| Variant | p95 latency | accuracy | fallback | cost/decision | bottleneck |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for name in ("baseline", "pooled"):
        item = report[name]
        bottleneck = item["phase_bottleneck"] or {}
        lines.append(
            f"| {name} | {item['latency_p95_ms']} ms | {item['decision_accuracy']} | "
            f"{item['fallback_rate']} | {item['mean_cost_per_decision_usd']} | "
            f"{bottleneck.get('phase', '—')} ({bottleneck.get('p95_ms', '—')} ms) |"
        )
    lines.extend([
        "",
        f"p95 delta: `{comparison['p95_delta_ms']}` ms; speedup: `{comparison['p95_speedup_fraction']}`.",
        "",
        "Checks: " + ", ".join(
            f"{key}={'PASS' if value else 'HOLD'}" for key, value in report["checks"].items()
        ) + ".",
        "",
        "Transport telemetry is raw-free and bounded. Production outcome labels are still required before broad default-on rollout.",
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
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--pooled", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-stage7/latency"))
    parser.add_argument("--target-p95-ms", type=float, default=5000.0)
    args = parser.parse_args()
    report = evaluate_latency_calibration(
        _load(args.baseline), _load(args.pooled),
        settings=LatencyCalibrationSettings(target_p95_ms=args.target_p95_ms),
    )
    paths = write_artifacts(report, args.output)
    print(f"Report: {paths[1]}; passed={report['promotion']['passed']}")


if __name__ == "__main__":
    main()


__all__ = [
    "LatencyCalibrationSettings",
    "SCHEMA",
    "evaluate_latency_calibration",
    "render_report",
    "write_artifacts",
]
