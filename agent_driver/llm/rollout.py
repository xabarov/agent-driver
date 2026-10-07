"""Rollout policy and evidence counters for opt-in JEV gates.

The runtime gates remain independently injectable.  This module provides the
small policy layer used to move those gates from ``off`` to ``shadow`` to
``active`` without changing task policy or model-provider wiring.  It is
deliberately raw-free: observations contain gate names, counts, costs and
bounded outcome labels, never prompts or answers.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
import json
from math import ceil, isfinite
from pathlib import Path
from threading import RLock
from typing import Literal

JevRolloutMode = Literal["off", "shadow", "active"]
JevGateName = Literal["routing", "quality", "compaction", "memory"]
JEV_ROLLOUT_SCHEMA = "jev-rollout.v1"
JEV_PRODUCTION_LABEL_SCHEMA = "jev-production-label.v1"
_GATES = frozenset({"routing", "quality", "compaction", "memory"})
_MODES = frozenset({"off", "shadow", "active"})
_LABEL_SOURCES = frozenset({"production", "production_review", "preproduction_review"})


def jev_task_category(app_metadata: Mapping[str, object] | None) -> str:
    """Resolve a bounded task category from host metadata."""
    if isinstance(app_metadata, Mapping):
        for key in ("jev_task", "task_category", "task_type"):
            value = app_metadata.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:64]
    return "normal_chat"


def resolve_jev_mode(
    policy: object | None,
    gate: str,
    *,
    task: str | None = None,
) -> JevRolloutMode:
    """Resolve a policy mode while preserving legacy behavior without policy."""
    if policy is None:
        return "active"
    mode_for = getattr(policy, "mode_for", None)
    if not callable(mode_for):
        return "off"
    try:
        mode = mode_for(gate, task=task)
    except Exception:  # noqa: BLE001 - malformed host policy must disable the gate
        return "off"
    return mode if mode in _MODES else "off"


def observe_jev_gate(
    policy: object | None,
    *,
    gate: str,
    task: str | None = None,
    fallback: bool = False,
    cost_usd: float | None = None,
    latency_ms: float | None = None,
) -> dict[str, object] | None:
    """Forward one raw-free outcome to a controller when one is configured."""
    observe = getattr(policy, "observe", None)
    if not callable(observe):
        return None
    try:
        result = observe(
            gate=gate,
            task=task,
            fallback=fallback,
            cost_usd=cost_usd,
            latency_ms=latency_ms,
        )
    except Exception:  # noqa: BLE001 - telemetry/rollback cannot break a run
        return None
    return result if isinstance(result, dict) else None


def _validate_mode(value: str, *, field_name: str) -> str:
    if value not in _MODES:
        raise ValueError(f"{field_name} must be one of off, shadow, active")
    return value


@dataclass(frozen=True, slots=True)
class JevRolloutSettings:
    """Per-task JEV rollout policy.

    ``mode`` is the default for enabled gates.  ``gate_modes`` can narrow one
    gate independently, while ``task_modes`` gives a task category its own
    mode.  A task allowlist, when non-empty, keeps all other tasks off.  The
    default is fully off, preserving existing behavior until a host opts in.
    """

    mode: JevRolloutMode = "off"
    enabled_gates: tuple[JevGateName, ...] = (
        "routing",
        "quality",
        "compaction",
        "memory",
    )
    task_allowlist: tuple[str, ...] = ()
    task_modes: Mapping[str, JevRolloutMode] = field(default_factory=dict)
    gate_modes: Mapping[JevGateName, JevRolloutMode] = field(default_factory=dict)
    pinned_models: Mapping[JevGateName, str] = field(default_factory=dict)
    max_cost_usd: float | None = None
    max_fallback_rate: float | None = None
    max_latency_p95_ms: float | None = None
    min_observations_for_rollback: int = 20
    rollback_on_breach: bool = True

    def __post_init__(self) -> None:
        _validate_mode(self.mode, field_name="mode")
        gates = tuple(dict.fromkeys(self.enabled_gates))
        if any(gate not in _GATES for gate in gates):
            raise ValueError(f"enabled_gates must use {_GATES}")
        object.__setattr__(self, "enabled_gates", gates)
        allowlist = tuple(dict.fromkeys(item.strip() for item in self.task_allowlist))
        if any(not item for item in allowlist):
            raise ValueError("task_allowlist entries must be non-empty")
        object.__setattr__(self, "task_allowlist", allowlist)
        task_modes = dict(self.task_modes)
        for task, value in task_modes.items():
            if not isinstance(task, str) or not task.strip():
                raise ValueError("task_modes keys must be non-empty strings")
            _validate_mode(value, field_name=f"task_modes[{task!r}]")
        object.__setattr__(self, "task_modes", task_modes)
        gate_modes = dict(self.gate_modes)
        for gate, value in gate_modes.items():
            if gate not in _GATES:
                raise ValueError(f"gate_modes must use {_GATES}")
            _validate_mode(value, field_name=f"gate_modes[{gate!r}]")
        object.__setattr__(self, "gate_modes", gate_modes)
        pinned = dict(self.pinned_models)
        for gate, model in pinned.items():
            if gate not in _GATES or not isinstance(model, str) or not model.strip():
                raise ValueError("pinned_models must map known gates to model ids")
        object.__setattr__(self, "pinned_models", pinned)
        if self.max_cost_usd is not None and self.max_cost_usd < 0:
            raise ValueError("max_cost_usd must be non-negative")
        if self.max_fallback_rate is not None and not 0 <= self.max_fallback_rate <= 1:
            raise ValueError("max_fallback_rate must be between 0 and 1")
        if self.max_latency_p95_ms is not None and self.max_latency_p95_ms < 0:
            raise ValueError("max_latency_p95_ms must be non-negative")
        if self.min_observations_for_rollback < 1:
            raise ValueError("min_observations_for_rollback must be positive")

    def mode_for(self, gate: str, *, task: str | None = None) -> JevRolloutMode:
        """Resolve the effective mode for one gate and task category."""
        if gate not in _GATES or gate not in self.enabled_gates:
            return "off"
        if self.task_allowlist and task not in self.task_allowlist:
            return "off"
        if task and task in self.task_modes:
            return self.task_modes[task]
        if gate in self.gate_modes:
            return self.gate_modes[gate]
        return self.mode

    def model_for(self, gate: str, default: str | None = None) -> str | None:
        """Return a pinned gate model, or the caller's configured default."""
        return self.pinned_models.get(gate, default)

    def metadata(self) -> dict[str, object]:
        """Return a raw-free policy projection suitable for run metadata."""
        return {
            "schema": JEV_ROLLOUT_SCHEMA,
            "mode": self.mode,
            "enabled_gates": list(self.enabled_gates),
            "task_allowlist": list(self.task_allowlist),
            "gate_modes": dict(self.gate_modes),
            "pinned_models": dict(self.pinned_models),
            "rollback_on_breach": self.rollback_on_breach,
            "max_cost_usd": self.max_cost_usd,
            "max_fallback_rate": self.max_fallback_rate,
            "max_latency_p95_ms": self.max_latency_p95_ms,
            "min_observations_for_rollback": self.min_observations_for_rollback,
        }


@dataclass(slots=True)
class JevRolloutController:
    """Collect rollout evidence and perform bounded automatic rollback."""

    settings: JevRolloutSettings
    _observations: int = 0
    _fallbacks: int = 0
    _cost_usd: float = 0.0
    _latencies_ms: list[float] = field(default_factory=list)
    _rolled_back: bool = False
    _rollback_reason: str | None = None

    def mode_for(self, gate: str, *, task: str | None = None) -> JevRolloutMode:
        if self._rolled_back:
            return "off"
        return self.settings.mode_for(gate, task=task)

    def observe(
        self,
        *,
        gate: str,
        task: str | None = None,
        fallback: bool = False,
        cost_usd: float | None = None,
        latency_ms: float | None = None,
    ) -> dict[str, object]:
        """Record one bounded gate outcome and return its current status."""
        self._observations += 1
        self._fallbacks += int(fallback)
        if cost_usd is not None and cost_usd >= 0:
            self._cost_usd += cost_usd
        if latency_ms is not None and 0 <= latency_ms:
            # Keep telemetry bounded even for a long-running process.
            self._latencies_ms.append(float(latency_ms))
            if len(self._latencies_ms) > 4096:
                del self._latencies_ms[: len(self._latencies_ms) - 4096]
        self._check_breach()
        return self.status(gate=gate, task=task)

    def rollback(self, reason: str) -> None:
        """Disable every JEV gate for this controller instance."""
        self._rolled_back = True
        self._rollback_reason = reason[:160] or "operator_rollback"

    def status(self, *, gate: str | None = None, task: str | None = None) -> dict[str, object]:
        rate = self._fallbacks / self._observations if self._observations else 0.0
        p95 = None
        if self._latencies_ms:
            ordered = sorted(self._latencies_ms)
            p95 = ordered[max(0, ceil(len(ordered) * 0.95) - 1)]
        return {
            "schema": JEV_ROLLOUT_SCHEMA,
            "gate": gate,
            "task": task,
            "mode": "off" if self._rolled_back else self.settings.mode,
            "rolled_back": self._rolled_back,
            "rollback_reason": self._rollback_reason,
            "observations": self._observations,
            "fallbacks": self._fallbacks,
            "fallback_rate": rate,
            "cost_usd": self._cost_usd,
            "cost_per_observation_usd": (
                self._cost_usd / self._observations if self._observations else 0.0
            ),
            "latency_p95_ms": p95,
            "thresholds": {
                "max_cost_usd": self.settings.max_cost_usd,
                "max_fallback_rate": self.settings.max_fallback_rate,
                "max_latency_p95_ms": self.settings.max_latency_p95_ms,
                "min_observations_for_rollback": self.settings.min_observations_for_rollback,
            },
            "threshold_breached": self._rollback_reason is not None,
        }

    def _check_breach(self) -> None:
        if self._rolled_back or not self.settings.rollback_on_breach:
            return
        if self._observations < self.settings.min_observations_for_rollback:
            return
        fallback_rate = self._fallbacks / self._observations
        if (
            self.settings.max_fallback_rate is not None
            and fallback_rate > self.settings.max_fallback_rate
        ):
            self.rollback("fallback_rate_limit")
        elif (
            self.settings.max_cost_usd is not None
            and self._cost_usd > self.settings.max_cost_usd
        ):
            self.rollback("cost_budget_exceeded")
        elif (
            self.settings.max_latency_p95_ms is not None
            and self._latencies_ms
            and float(self.status()["latency_p95_ms"]) > self.settings.max_latency_p95_ms
        ):
            self.rollback("latency_p95_limit")


@dataclass(frozen=True, slots=True)
class JevOutcomeLabel:
    """External, raw-free outcome label for one completed JEV decision."""

    label_id: str
    decision_id: str
    gate: JevGateName
    task: str
    correct: bool
    safety_passed: bool
    fallback: bool
    latency_ms: float | None = None
    cost_usd: float | None = None
    source: str = "production_review"
    window_id: str = "default"
    observed_at: str = ""

    def __post_init__(self) -> None:
        for name in ("label_id", "decision_id", "task", "window_id"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError(f"{name} must be a bounded non-empty string")
        if self.gate not in _GATES:
            raise ValueError(f"unknown gate: {self.gate}")
        if self.source not in _LABEL_SOURCES:
            raise ValueError("unknown label source")
        for name in ("correct", "safety_passed", "fallback"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError("label outcomes must be booleans")
        for name in ("latency_ms", "cost_usd"):
            value = getattr(self, name)
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, (int, float))
                or not isfinite(value) or value < 0
            ):
                raise ValueError(f"{name} must be finite and non-negative")

    def to_dict(self) -> dict[str, object]:
        return {
            "schema": JEV_PRODUCTION_LABEL_SCHEMA,
            "label_id": self.label_id,
            "decision_id": self.decision_id,
            "gate": self.gate,
            "task": self.task,
            "correct": self.correct,
            "safety_passed": self.safety_passed,
            "fallback": self.fallback,
            "latency_ms": self.latency_ms,
            "cost_usd": self.cost_usd,
            "source": self.source,
            "window_id": self.window_id,
            "observed_at": self.observed_at or datetime.now(UTC).isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, object]) -> "JevOutcomeLabel":
        if payload.get("schema") != JEV_PRODUCTION_LABEL_SCHEMA:
            raise ValueError("invalid production label schema")
        allowed = {
            "schema", "label_id", "decision_id", "gate", "task", "correct",
            "safety_passed", "fallback", "latency_ms", "cost_usd", "source",
            "window_id", "observed_at",
        }
        if set(payload) - allowed:
            raise ValueError("production labels must not contain raw state")
        values = {
            key: payload[key]
            for key in allowed
            if key != "schema" and key in payload
        }
        if not all(isinstance(values.get(key), bool) for key in ("correct", "safety_passed", "fallback")):
            raise ValueError("label outcomes must be booleans")
        return cls(**values)  # type: ignore[arg-type]


class JevProductionLabelLedger:
    """Append-only, deduplicated production label store."""

    def __init__(self, path: str | Path, *, max_bytes: int = 10_000_000) -> None:
        self.path = Path(path)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        self.max_bytes = max_bytes
        self._lock = RLock()

    def append(self, label: JevOutcomeLabel) -> bool:
        """Append a label once; return false for a duplicate label id."""
        with self._lock:
            labels = self.read()
            for item in labels:
                if item.label_id == label.label_id or (
                    item.gate == label.gate and item.decision_id == label.decision_id
                ):
                    if _same_label_outcome(item, label):
                        return False
                    raise ValueError("conflicting label for an existing decision")
            encoded = (json.dumps(label.to_dict(), sort_keys=True, ensure_ascii=False) + "\n").encode()
            current = self.path.stat().st_size if self.path.exists() else 0
            if current + len(encoded) > self.max_bytes:
                raise ValueError("production label ledger exceeds max_bytes")
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("ab") as handle:
                handle.write(encoded)
            return True

    def read(self) -> list[JevOutcomeLabel]:
        if not self.path.exists():
            return []
        if self.path.stat().st_size > self.max_bytes:
            raise ValueError("production label ledger exceeds max_bytes")
        labels: list[JevOutcomeLabel] = []
        with self.path.open("rb") as handle:
            for line in handle:
                if not line.strip():
                    continue
                payload = json.loads(line)
                if not isinstance(payload, dict):
                    raise ValueError("production label line must be an object")
                labels.append(JevOutcomeLabel.from_dict(payload))
        return unique_outcome_labels(labels)

    def snapshot(self) -> dict[str, object]:
        labels = self.read()
        return {
            "schema": JEV_PRODUCTION_LABEL_SCHEMA,
            "labels": len(labels),
            "windows": sorted({item.window_id for item in labels}),
            "sources": sorted({item.source for item in labels}),
        }


def _same_label_outcome(left: JevOutcomeLabel, right: JevOutcomeLabel) -> bool:
    # A repeated review must not inflate the sample count, even if its label id
    # or review timestamp differs. Conflicting reviews require reconciliation.
    ignored = {"label_id", "observed_at"}
    return {
        key: value for key, value in left.to_dict().items() if key not in ignored
    } == {
        key: value for key, value in right.to_dict().items() if key not in ignored
    }


def unique_outcome_labels(labels: list[JevOutcomeLabel]) -> list[JevOutcomeLabel]:
    """Count each decision once; reject conflicting reviews and reused ids."""
    by_id: dict[str, JevOutcomeLabel] = {}
    by_decision: dict[tuple[str, str], JevOutcomeLabel] = {}
    result = []
    for label in labels:
        key = (label.gate, label.decision_id)
        previous = by_id.get(label.label_id) or by_decision.get(key)
        if previous is not None:
            if not _same_label_outcome(previous, label):
                raise ValueError("conflicting label for an existing decision")
        else:
            result.append(label)
        by_id[label.label_id] = label
        by_decision[key] = label
    return result


__all__ = [
    "JEV_PRODUCTION_LABEL_SCHEMA",
    "JEV_ROLLOUT_SCHEMA",
    "JevGateName",
    "JevRolloutController",
    "JevRolloutMode",
    "JevRolloutSettings",
    "JevOutcomeLabel",
    "JevProductionLabelLedger",
    "jev_task_category",
    "observe_jev_gate",
    "resolve_jev_mode",
    "unique_outcome_labels",
]
