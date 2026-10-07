"""Bounded decision telemetry. No state, questions, bodies or credentials are kept."""
from __future__ import annotations

from collections import Counter, deque
from math import ceil, isfinite
from threading import Lock
from time import monotonic


def finite_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if isfinite(value) and value >= 0 else None


def latency_summary(values: list[float]) -> dict[str, float | int | None]:
    ordered = sorted(values)
    return {
        "samples": len(ordered),
        **{
            f"p{q}_ms": ordered[ceil(len(ordered) * q / 100) - 1] if ordered else None
            for q in (50, 95, 99)
        },
        "max_ms": max(ordered) if ordered else None,
    }


class DecisionTiming:
    """Per-call timers; httpcore trace payloads are deliberately ignored."""

    PHASES = ("client_setup_ms", "connect_tcp_ms", "start_tls_ms",
              "receive_response_headers_ms", "receive_response_body_ms",
              "http_ms", "retry_wait_ms", "decode_validate_ms")

    def __init__(self) -> None:
        self.started = monotonic()
        self.phases = dict.fromkeys(self.PHASES, 0.0)
        self.starts: dict[str, float] = {}
        self.attempts = 0
        self.status_code: int | None = None
        self.connections = 0

    async def trace(self, event: str, info: dict) -> None:
        parts = event.split(".")
        if len(parts) < 2:
            return
        phase, state = parts[-2:]
        if f"{phase}_ms" not in self.phases:
            return
        if state == "started":
            self.starts[phase] = monotonic()
            if phase == "connect_tcp":
                self.connections += 1
        elif state in {"complete", "failed"} and phase in self.starts:
            self.phases[f"{phase}_ms"] += (monotonic() - self.starts.pop(phase)) * 1000

    def finish(self) -> dict[str, float]:
        now = monotonic()
        for phase, started in self.starts.items():
            self.phases[f"{phase}_ms"] += (now - started) * 1000
        self.starts.clear()
        return {key: round(value, 3) for key, value in self.phases.items()}


class DecisionTelemetry:
    """Thread-safe process counters and a bounded recent-call window."""

    def __init__(self, *, window_size: int = 512) -> None:
        if type(window_size) is not int or window_size < 1:
            raise ValueError("window_size must be positive")
        self._rows: deque[dict] = deque(maxlen=window_size)
        self._outcomes: Counter[str] = Counter()
        self._known_cost = 0.0
        self._unknown_cost = 0
        self._lock = Lock()

    def record(self, *, timing: DecisionTiming, outcome: str, cost_usd: float | None,
               model: str | None = None, model_version: str | None = None) -> None:
        # All callers are internal; whitelist outcome labels to avoid error-message leakage.
        allowed = {"success", "timeout", "transport_error", "request_rejected",
                   "invalid_response", "circuit_open", "cancelled", "internal_error"}
        cost = finite_number(cost_usd)
        row = {
            "outcome": outcome if outcome in allowed else "internal_error",
            "latency_ms": round((monotonic() - timing.started) * 1000, 3),
            "phases": timing.finish(), "attempts": timing.attempts,
            "status_code": timing.status_code, "connections": timing.connections,
            "cost_usd": cost, "model": model, "model_version": model_version,
        }
        with self._lock:
            self._rows.append(row)
            self._outcomes[row["outcome"]] += 1
            self._known_cost += cost or 0.0
            self._unknown_cost += int(cost is None)

    def snapshot(self) -> dict:
        with self._lock:
            rows = list(self._rows)
            return {
                "schema": "jev-transport-telemetry.v1", "scope": "process",
                "calls": sum(self._outcomes.values()), "outcomes": dict(self._outcomes),
                "known_cost_usd": self._known_cost, "unknown_cost_calls": self._unknown_cost,
                "window_size": self._rows.maxlen,
                "latency": latency_summary([row["latency_ms"] for row in rows]),
                "phases": {phase: latency_summary([row["phases"][phase] for row in rows])
                           for phase in DecisionTiming.PHASES},
                "rows": rows,
            }
