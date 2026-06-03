from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_matrix_module():
    path = (
        Path(__file__).resolve().parents[2] / "scripts" / "deep_research_live_matrix.py"
    )
    spec = importlib.util.spec_from_file_location(
        "deep_research_live_matrix_test", path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_acceptance_axes_require_trace_artifacts_budget_and_grounding(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")
    (artifact_dir / "workspace-preview.json").write_text(
        "https://example.com/source", encoding="utf-8"
    )

    acceptance = matrix.acceptance_axes(
        profile="medium",
        question={
            "source_urls": ["https://example.com/source"],
            "budgets": {"medium": 100},
        },
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "failures": {"missing_terminal_event": False},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "llm": {"usage": {"total_tokens": 99}},
        },
    )

    assert acceptance == {
        "expected": True,
        "trace": True,
        "artifact": True,
        "ui": True,
        "budget": True,
        "grounding": True,
    }


def test_acceptance_error_lists_failed_axes() -> None:
    matrix = _load_matrix_module()

    assert matrix.acceptance_error({"expected": True, "trace": False}) == (
        "acceptance failed: trace"
    )


def test_medium_acceptance_requires_parent_report_write_evidence(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")
    (artifact_dir / "workspace-artifacts.json").write_text(
        """
        {
          "artifacts": [
            {"path": "research/report.md"},
            {"path": "research/sources.jsonl"}
          ]
        }
        """,
        encoding="utf-8",
    )

    acceptance = matrix.acceptance_axes(
        profile="medium",
        question={},
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "failures": {},
            "artifacts": {"paths": []},
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is False


def test_acceptance_artifacts_can_pair_workspace_index_with_trace_write(
    tmp_path,
) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")
    (artifact_dir / "workspace-artifacts.json").write_text(
        """
        {
          "artifacts": [
            {"path": "research/report.md"},
            {"path": "research/sources.jsonl"}
          ]
        }
        """,
        encoding="utf-8",
    )

    acceptance = matrix.acceptance_axes(
        profile="medium",
        question={},
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "failures": {},
            "artifacts": {"paths": [], "report_write_seen": True},
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is True


def test_budget_fails_closed_for_medium_without_usage(tmp_path) -> None:
    matrix = _load_matrix_module()

    assert (
        matrix.budget_ok(
            profile="medium",
            question={},
            summary={"llm": {"usage": {}}},
        )
        is False
    )


def test_grounding_normalizes_url_trailing_slashes(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "trace-summary.json").write_text(
        '{"source":"https://Example.com/source/"}',
        encoding="utf-8",
    )

    assert (
        matrix.grounding_ok(
            question={"source_urls": ["https://example.com/source"]},
            artifact_dir=artifact_dir,
        )
        is True
    )


def test_hard_profile_requires_claims_artifact(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")

    acceptance = matrix.acceptance_axes(
        profile="hard",
        question={},
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "failures": {},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is False
