"""Controlled canary evaluation and production calibration for JEV.

Stage 6 consumes the raw-free cross-surface live validation artifact.  It does
not issue provider requests itself: a bounded canary is captured once, then
this module applies explicit SLO gates and produces a promotion decision.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from math import ceil
from pathlib import Path
from statistics import mean
from typing import Any

from agent_driver.evals.jev_stage5 import ThresholdSample, calibrate_jev_thresholds
from agent_driver.llm.rollout import JevRolloutSettings

SCHEMA = "jev-stage6-canary.v1"
_GATES = ("routing", "quality", "compaction", "memory")
_SURFACE_TO_GATE = {
    "routing": "routing",
    "quality": "quality",
    "recovery": "quality",
    "compaction": "compaction",
    "memory": "memory",
}


@dataclass(frozen=True, slots=True)
class JevCanarySettings:
    """Safe initial canary policy for a bounded task category."""

    task_allowlist: tuple[str, ...] = ("normal_chat",)
    gate_modes: dict[str, str] = field(
        default_factory=lambda: {
            "routing": "active",
            "quality": "active",
            "compaction": "shadow",
            "memory": "shadow",
        }
    )
    sample_budget: int = 50
    max_fallback_rate: float = 0.05
    max_latency_p95_ms: float = 5000.0
    max_cost_per_decision_usd: float = 0.0001
    min_decision_accuracy: float = 0.95
    require_invariants: bool = True
    min_labeled_samples: int = 4

    def __post_init__(self) -> None:
        allowlist = tuple(dict.fromkeys(item.strip() for item in self.task_allowlist))
        if any(not item for item in allowlist):
            raise ValueError("task_allowlist entries must be non-empty")
        object.__setattr__(self, "task_allowlist", allowlist)
        modes = dict(self.gate_modes)
        if set(modes) - set(_GATES):
            raise ValueError(f"gate_modes must use {set(_GATES)}")
        if any(value not in {"off", "shadow", "active"} for value in modes.values()):
            raise ValueError("gate_modes values must be off, shadow, or active")
        object.__setattr__(self, "gate_modes", modes)
        if self.sample_budget < 1:
            raise ValueError("sample_budget must be positive")
        if not 0 <= self.max_fallback_rate <= 1:
            raise ValueError("max_fallback_rate must be between 0 and 1")
        if self.max_latency_p95_ms < 0 or self.max_cost_per_decision_usd < 0:
            raise ValueError("latency and cost limits must be non-negative")
        if not 0 <= self.min_decision_accuracy <= 1:
            raise ValueError("min_decision_accuracy must be between 0 and 1")
        if self.min_labeled_samples < 1:
            raise ValueError("min_labeled_samples must be positive")

    def rollout_settings(self, decision_model: str) -> JevRolloutSettings:
        """Return the runtime policy represented by this canary."""
        return JevRolloutSettings(
            mode="off",
            task_allowlist=self.task_allowlist,
            gate_modes=self.gate_modes,
            pinned_models={gate: decision_model for gate in _GATES},
            max_cost_usd=self.max_cost_per_decision_usd * self.sample_budget,
            max_fallback_rate=self.max_fallback_rate,
            max_latency_p95_ms=self.max_latency_p95_ms,
            min_observations_for_rollback=1,
        )

    def metadata(self, decision_model: str) -> dict[str, Any]:
        return {
            "schema": SCHEMA,
            "task_allowlist": list(self.task_allowlist),
            "gate_modes": dict(self.gate_modes),
            "sample_budget": self.sample_budget,
            "max_fallback_rate": self.max_fallback_rate,
            "max_latency_p95_ms": self.max_latency_p95_ms,
            "max_cost_per_decision_usd": self.max_cost_per_decision_usd,
            "min_decision_accuracy": self.min_decision_accuracy,
            "require_invariants": self.require_invariants,
            "min_labeled_samples": self.min_labeled_samples,
            "runtime_policy": self.rollout_settings(decision_model).metadata(),
        }


def _active_rows(report: dict[str, Any]) -> list[dict[str, Any]]:
    rows = report.get("rows", [])
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("treatments"), list)
        and len(row["treatments"]) >= 3
        and isinstance(row["treatments"][2], dict)
    ]


def _p95(values: list[float]) -> float | None:
    if not values:
        return None
    return sorted(values)[max(0, ceil(len(values) * 0.95) - 1)]


def _number(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) else None


def _calibrate(rows: list[dict[str, Any]], minimum: int) -> dict[str, dict[str, Any]]:
    grouped: dict[str, list[ThresholdSample]] = defaultdict(list)
    for row in rows:
        gate = _SURFACE_TO_GATE.get(str(row.get("surface")))
        active = row["treatments"][2]
        confidence = _number(active.get("confidence"))
        correct = active.get("decision_correct")
        if gate and confidence is not None and isinstance(correct, bool):
            grouped[gate].append(
                ThresholdSample(
                    gate=gate, confidence=max(0.0, min(1.0, confidence)),
                    expected_accept=correct, predicted_correct=True,
                )
            )
    calibrated = calibrate_jev_thresholds(
        tuple(sample for values in grouped.values() for sample in values)
    )
    for gate, item in calibrated.items():
        item["source"] = "live_canary_oracle"
        item["status"] = "calibrated" if item["samples"] >= minimum else "insufficient_evidence"
    return calibrated


def evaluate_stage6_canary(
    live_validation: dict[str, Any],
    *,
    stage5_report: dict[str, Any] | None = None,
    chat_demo_check: dict[str, Any] | None = None,
    settings: JevCanarySettings | None = None,
    decision_model: str | None = None,
) -> dict[str, Any]:
    """Apply production-style gates to one bounded live canary artifact."""
    settings = settings or JevCanarySettings()
    decision_model = decision_model or str(live_validation.get("decision_model", "unknown"))
    rows = _active_rows(live_validation)
    calls = [row.get("call", {}) for row in rows if isinstance(row.get("call"), dict)]
    latencies = [value for call in calls if (value := _number(call.get("latency_ms"))) is not None]
    costs = [value for call in calls if (value := _number(call.get("cost_usd"))) is not None]
    correct = [row["treatments"][2].get("decision_correct") for row in rows]
    correct = [value for value in correct if isinstance(value, bool)]
    fallbacks = [bool(row["treatments"][2].get("fallback")) for row in rows]
    accuracy = mean(correct) if correct else None
    fallback_rate = mean(fallbacks) if fallbacks else None
    p95_latency = _p95(latencies)
    mean_cost = mean(costs) if costs else None
    invariants = all(
        all(item.get("invariants", {}).values())
        for row in rows
        for item in row.get("treatments", [])
        if isinstance(item, dict)
    )
    shadow_unchanged = all(bool(row.get("shadow_behavior_unchanged")) for row in rows)
    paired_replay = all(bool(row.get("paired_replay_valid")) for row in rows)
    active_effects = sum(bool(row.get("active_effect_observed")) for row in rows)
    stage5_ready = (
        stage5_report is None
        or stage5_report.get("rollout", {}).get("active_low_risk_ready") is True
    )
    chat_ready = (
        chat_demo_check is None
        or chat_demo_check.get("passed") is True
        and chat_demo_check.get("raw_free") is True
    )
    checks = {
        "source_validation": bool(live_validation.get("passed")),
        "sample_budget": len(calls) <= settings.sample_budget,
        "decision_accuracy": accuracy is not None and accuracy >= settings.min_decision_accuracy,
        "fallback_rate": fallback_rate is not None and fallback_rate <= settings.max_fallback_rate,
        "latency_p95": p95_latency is not None and p95_latency <= settings.max_latency_p95_ms,
        "cost_per_decision": mean_cost is not None and mean_cost <= settings.max_cost_per_decision_usd,
        "invariants": invariants if settings.require_invariants else True,
        "shadow_behavior_unchanged": shadow_unchanged,
        "paired_replay": paired_replay,
        "stage5_active_low_risk": stage5_ready,
        "chat_demo": chat_ready,
    }
    reasons = [name for name, passed in checks.items() if not passed]
    gate_evidence = {
        gate: {
            "mode": settings.gate_modes.get(gate, "off"),
            "observed_rows": sum(
                _SURFACE_TO_GATE.get(str(row.get("surface"))) == gate for row in rows
            ),
            "promotion": "active" if settings.gate_modes.get(gate) == "active" else "held",
        }
        for gate in _GATES
    }
    metrics = {
        "cases": len({row.get("case_id") for row in rows}),
        "repeats": len({row.get("repeat") for row in rows}),
        "decision_calls": len(calls),
        "decision_accuracy": accuracy,
        "fallback_rate": fallback_rate,
        "latency_p95_ms": p95_latency,
        "mean_cost_per_decision_usd": mean_cost,
        "known_cost_usd": sum(costs),
        "invariants_passed": invariants,
        "shadow_behavior_unchanged": shadow_unchanged,
        "paired_replay_valid": paired_replay,
        "active_effects_observed": active_effects,
        "source_passed": bool(live_validation.get("passed")),
    }
    passed = not reasons and bool(rows)
    return {
        "schema": SCHEMA,
        "created_at": datetime.now(UTC).isoformat(),
        "live": bool(live_validation.get("live")),
        "decision_model": decision_model,
        "settings": settings.metadata(decision_model),
        "metrics": metrics,
        "checks": checks,
        "calibration": {
            "source": "live_canary_oracle",
            "minimum_labeled_samples": settings.min_labeled_samples,
            "gates": _calibrate(rows, settings.min_labeled_samples),
        },
        "gates": gate_evidence,
        "promotion": {
            "passed": passed,
            "recommendation": "canary_active" if passed else "shadow",
            "rollback_required": bool(reasons),
            "reasons": reasons,
            "task_allowlist": list(settings.task_allowlist),
            "active_gates": [gate for gate, mode in settings.gate_modes.items() if mode == "active"],
            "held_gates": [gate for gate, mode in settings.gate_modes.items() if mode != "active"],
        },
        "rows": [
            {
                "case_id": row.get("case_id"),
                "repeat": row.get("repeat"),
                "surface": row.get("surface"),
                "categories": row.get("categories", []),
                "active": {
                    key: row["treatments"][2].get(key)
                    for key in ("outcome", "decision_correct", "fallback", "confidence", "source")
                },
                "shadow_behavior_unchanged": bool(row.get("shadow_behavior_unchanged")),
                "active_effect_observed": bool(row.get("active_effect_observed")),
                "paired_replay_valid": bool(row.get("paired_replay_valid")),
            }
            for row in rows
        ],
    }


def load_live_validation(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("schema") != "jev-live-validation.v1":
        raise ValueError(f"invalid live validation artifact: {path}")
    return payload


def _load_optional(path: Path | None) -> dict[str, Any] | None:
    if path is None:
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"invalid JSON artifact: {path}")
    return payload


def render_stage6_report(report: dict[str, Any]) -> str:
    metrics = report["metrics"]
    promotion = report["promotion"]
    lines = [
        "# JEV Stage 6 controlled canary report",
        "",
        f"Run: {report['created_at']}; live: `{report['live']}`; model: `{report['decision_model']}`.",
        "",
        "The canary is task-scoped and raw-free. Routing and quality are active; compaction and memory remain shadow until a separate evidence gate is approved.",
        "",
        f"Promotion gate: **{'PASS' if promotion['passed'] else 'HOLD'}** (`{promotion['recommendation']}`).",
        "",
        "| Metric | Value |",
        "|---|---:|",
    ]
    for key in (
        "decision_calls", "cases", "repeats", "decision_accuracy", "fallback_rate",
        "latency_p95_ms", "mean_cost_per_decision_usd", "known_cost_usd",
        "invariants_passed", "shadow_behavior_unchanged", "paired_replay_valid",
        "active_effects_observed",
    ):
        lines.append(f"| {key} | {metrics.get(key)} |")
    lines.extend([
        "",
        "Checks: " + ", ".join(
            f"{key}={'PASS' if value else 'HOLD'}" for key, value in report["checks"].items()
        ) + ".",
        "Rollback reasons: " + (", ".join(promotion["reasons"]) if promotion["reasons"] else "none") + ".",
        "",
        "| Gate | Mode | Observed rows | Decision | Calibration |",
        "|---|---|---:|---|---|",
    ])
    for gate, item in report["gates"].items():
        calibration = report["calibration"]["gates"].get(gate, {})
        lines.append(
            f"| {gate} | {item['mode']} | {item['observed_rows']} | {item['promotion']} | "
            f"{calibration.get('status', 'no_labels')} ({calibration.get('samples', 0)} samples) |"
        )
    lines.extend([
        "",
        "Calibration labels come from the bounded canary oracle and are suitable for pre-production tuning; production labels should replace them before broad enablement.",
        "",
    ])
    return "\n".join(lines)


def write_stage6_artifacts(report: dict[str, Any], output: Path) -> tuple[Path, Path]:
    output.mkdir(parents=True, exist_ok=True)
    json_path = output / "report.json"
    markdown_path = output / "report.md"
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    markdown_path.write_text(render_stage6_report(report), encoding="utf-8")
    return json_path, markdown_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-validation", type=Path, required=True)
    parser.add_argument("--stage5-report", type=Path)
    parser.add_argument("--chat-demo-check", type=Path)
    parser.add_argument("--output", type=Path, default=Path("artifacts/jev-stage6/canary"))
    parser.add_argument("--sample-budget", type=int, default=50)
    args = parser.parse_args()
    if args.sample_budget < 1:
        parser.error("sample budget must be positive")
    live = load_live_validation(args.live_validation)
    settings = JevCanarySettings(sample_budget=args.sample_budget)
    report = evaluate_stage6_canary(
        live,
        stage5_report=_load_optional(args.stage5_report),
        chat_demo_check=_load_optional(args.chat_demo_check),
        settings=settings,
    )
    paths = write_stage6_artifacts(report, args.output)
    print(f"Report: {paths[1]}; passed={report['promotion']['passed']}")


if __name__ == "__main__":
    main()


__all__ = [
    "JevCanarySettings",
    "SCHEMA",
    "evaluate_stage6_canary",
    "load_live_validation",
    "render_stage6_report",
    "write_stage6_artifacts",
]
