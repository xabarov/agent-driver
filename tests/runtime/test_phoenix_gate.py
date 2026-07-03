"""Phoenix validation gate tests."""

from __future__ import annotations

import json

from agent_driver.runtime.phoenix_gate import (
    build_phoenix_trace_gate,
    write_phoenix_gate_artifacts,
)


def test_phoenix_gate_skips_without_live_claim() -> None:
    gate = build_phoenix_trace_gate(live_claim=False, span_count=0)

    assert gate.status == "skipped"
    assert gate.reason == "no_live_runtime_or_provider_claim"


def test_phoenix_gate_fails_zero_spans_for_live_claim() -> None:
    gate = build_phoenix_trace_gate(live_claim=True, span_count=0)

    assert gate.status == "failed"
    assert gate.reason == "phoenix_zero_spans_for_live_claim"


def test_phoenix_gate_passes_and_persists_trace_ids(tmp_path) -> None:
    result = write_phoenix_gate_artifacts(
        tmp_path / "phoenix",
        live_claim=True,
        span_count=3,
        trace_ids=["trace_1"],
        endpoint="http://127.0.0.1:6006",
        project_name="agent-driver",
    )

    assert result["gate"]["status"] == "passed"
    assert result["validation_gates"]["statuses"]["phoenix_trace"] == "passed"
    root = tmp_path / "phoenix"
    assert (root / "manifest.json").is_file()
    assert json.loads((root / "phoenix_run_ids.json").read_text()) == {
        "run_ids": ["trace_1"]
    }
