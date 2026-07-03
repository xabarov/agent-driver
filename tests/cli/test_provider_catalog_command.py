"""CLI smoke tests for deterministic provider-catalog artifacts."""

from __future__ import annotations

import json

from agent_driver.cli.main import main


def test_provider_catalog_audit_writes_artifacts(tmp_path, capsys) -> None:
    code = main(
        [
            "provider-catalog",
            "audit",
            "--scenario",
            "provider_catalog.sanitizer_matrix.v1",
            "--no-live",
            "--output-dir",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert code == 0
    assert payload["mode"] == "deterministic"
    assert payload["report"]["live_status"] == "no_claim"
    assert (tmp_path / "provider_compatibility_report.json").is_file()
    assert (tmp_path / "provider_sanitizer_matrix.json").is_file()
    assert (tmp_path / "validation_gates.json").is_file()
