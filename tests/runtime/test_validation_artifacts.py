"""Validation artifact persistence tests."""

from __future__ import annotations

import hashlib
import json

import pytest

from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def test_write_validation_artifacts_persists_manifest_and_files(tmp_path) -> None:
    screenshot = tmp_path / "ui.png"
    screenshot.write_bytes(b"fake-png")
    output_dir = tmp_path / "evidence"

    manifest = write_validation_artifacts(
        output_dir,
        support_bundle={"run_id": "run_1", "policy_evaluations": {"count": 1}},
        trace_summary={"run_id": "run_1", "validation_gates": {"count": 2}},
        validation_gates={"statuses": {"deterministic_tests": "passed"}},
        evidence_index={"index_id": "idx_1", "skipped_gate_ids": ["phoenix_trace"]},
        benchmark_json={"policy_decisions": {"required_source_evidence": 1}},
        benchmark_markdown="# Benchmark\n",
        phoenix_run_ids=["phoenix-run-1"],
        playwright_screenshots=[screenshot],
    )

    assert manifest["artifact_count"] == 8
    manifest_path = output_dir / "manifest.json"
    assert manifest_path.is_file()
    written_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert written_manifest == manifest

    assert json.loads((output_dir / "support_bundle.json").read_text())[
        "run_id"
    ] == "run_1"
    assert (output_dir / "benchmark_report.md").read_text(encoding="utf-8") == (
        "# Benchmark\n"
    )
    assert json.loads((output_dir / "evidence_index.json").read_text())[
        "index_id"
    ] == "idx_1"
    copied = output_dir / "playwright_screenshots" / "ui.png"
    assert copied.read_bytes() == b"fake-png"

    rows_by_type = {row["artifact_type"]: row for row in manifest["artifacts"]}
    assert rows_by_type["playwright_screenshot"]["path"] == (
        "playwright_screenshots/ui.png"
    )
    assert rows_by_type["playwright_screenshot"]["sha256"] == hashlib.sha256(
        b"fake-png"
    ).hexdigest()


def test_write_validation_artifacts_rejects_non_json_payload(tmp_path) -> None:
    with pytest.raises(ValueError):
        write_validation_artifacts(
            tmp_path / "evidence",
            support_bundle={"bad": object()},
        )


def test_write_validation_artifacts_persists_extra_json_artifacts(tmp_path) -> None:
    manifest = write_validation_artifacts(
        tmp_path / "evidence",
        extra_json_artifacts={"phoenix_ui_review": {"passed": True}},
    )

    assert manifest["artifact_count"] == 1
    row = manifest["artifacts"][0]
    assert row["artifact_type"] == "phoenix_ui_review"
    assert row["path"] == "phoenix_ui_review.json"
    payload = json.loads((tmp_path / "evidence" / "phoenix_ui_review.json").read_text())
    assert payload == {"passed": True}


def test_write_validation_artifacts_persists_command_outputs(tmp_path) -> None:
    manifest = write_validation_artifacts(
        tmp_path / "evidence",
        command_outputs=[
            {
                "command_id": "deterministic_1",
                "command": "python -m pytest tests/example.py",
                "status": "passed",
                "stdout": "ok",
                "stderr": "",
            }
        ],
    )

    assert manifest["artifact_count"] == 1
    row = manifest["artifacts"][0]
    assert row["artifact_type"] == "command_output"
    assert row["path"] == "command_outputs/deterministic_1.json"
    payload = json.loads(
        (tmp_path / "evidence" / "command_outputs" / "deterministic_1.json").read_text()
    )
    assert payload["status"] == "passed"
