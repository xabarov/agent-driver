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
            "terminal_event": "run_completed",
            "failures": {"missing_terminal_event": False},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 2,
                "child_fetch_count": 1,
                "parent_fetch_count": 1,
                "report_status": "verified",
            },
            "llm": {"usage": {"total_tokens": 99}},
        },
    )

    assert acceptance == {
        "expected": True,
        "trace": True,
        "artifact": True,
        "synthesis": True,
        "ledger": True,
        "evidence_split": True,
        "terminal": True,
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
            "terminal_event": "run_completed",
            "failures": {},
            "artifacts": {"paths": []},
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "child_fetch_count": 1,
            },
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
            "terminal_event": "run_completed",
            "failures": {},
            "artifacts": {"paths": [], "report_write_seen": True},
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "child_fetch_count": 1,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is True


def test_medium_acceptance_requires_non_empty_source_ledger(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")

    acceptance = matrix.acceptance_axes(
        profile="medium",
        question={},
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "terminal_event": "run_completed",
            "failures": {},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 0,
                "child_fetch_count": 1,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["ledger"] is False


def test_medium_acceptance_fails_synthesis_when_child_result_unused(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    artifact_dir.mkdir()
    (artifact_dir / "screenshot.png").write_bytes(b"png")

    acceptance = matrix.acceptance_axes(
        profile="medium",
        question={},
        artifact_dir=artifact_dir,
        expected_found=True,
        summary={
            "verdict": "pass",
            "terminal_event": "run_completed",
            "failures": {},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "child_fetch_count": 1,
                "child_result_not_used": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["synthesis"] is False


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
            "terminal_event": "run_completed",
            "failures": {},
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "child_fetch_count": 1,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is False


def test_result_json_and_markdown_surface_failure_context() -> None:
    matrix = _load_matrix_module()
    result = matrix.MatrixResult(
        scenario="deep-medium-fork",
        profile="medium",
        question_id="fork",
        repetition=1,
        ok=False,
        run_id="run_1",
        expected_found=True,
        acceptance={"trace": False, "ledger": False},
        error=None,
        artifact_dir="/tmp/run",
        trace_summary={
            "terminal_event": "run_completed",
            "failures": {"deep_research_no_source_ledger_artifact": True},
            "research_efficiency": {
                "source_ledger_record_count": 0,
                "parent_fetch_count": 2,
                "child_fetch_count": 1,
                "report_status": "draft",
            },
            "llm": {
                "usage": {"total_tokens": 42},
                "request_allowed_tools": [["file_write"], []],
                "request_tool_names": [["file_write"], []],
                "tool_choice_effective": [
                    {"type": "tool", "name": "file_write"},
                    "none",
                ],
            },
        },
    )

    payload = matrix.result_to_json(result)
    markdown = matrix.render_matrix_markdown([result])

    assert payload["failure_class"] == "artifact_contract"
    assert payload["failure_keys"] == ["deep_research_no_source_ledger_artifact"]
    assert payload["acceptance_failures"] == ["ledger", "trace"]
    assert payload["llm_request_allowed_tools"] == [["file_write"], []]
    assert payload["llm_request_tool_names"] == [["file_write"], []]
    assert payload["llm_tool_choice_effective"] == [
        {"type": "tool", "name": "file_write"},
        "none",
    ]
    assert payload["source_records"] == 0
    assert "sources=0" in markdown
    assert "deep_research_no_source_ledger_artifact" in markdown
