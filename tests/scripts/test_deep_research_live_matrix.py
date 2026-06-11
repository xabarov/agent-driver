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
                "final_references_report_artifact": True,
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
        "handoff": True,
        "terminal": True,
        "ui": True,
        "budget": True,
        "grounding": True,
        "hard_claims": True,
        "hard_safety": True,
    }


def test_acceptance_error_lists_failed_axes() -> None:
    matrix = _load_matrix_module()

    assert matrix.acceptance_error({"expected": True, "trace": False}) == (
        "acceptance failed: trace"
    )


def test_failed_acceptance_keeps_hard_axes_non_blocking_for_medium() -> None:
    matrix = _load_matrix_module()

    medium = matrix.failed_acceptance(profile="medium")
    hard = matrix.failed_acceptance(profile="hard")

    assert medium["hard_claims"] is True
    assert medium["hard_safety"] is True
    assert hard["hard_claims"] is False
    assert hard["hard_safety"] is False


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
                "final_references_report_artifact": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["artifact"] is False
    assert acceptance["hard_claims"] is True


def test_hard_profile_safety_axis_rejects_browser_violations(tmp_path) -> None:
    matrix = _load_matrix_module()

    assert (
        matrix.hard_safety_ok(
            profile="hard",
            summary={
                "research_efficiency": {
                    "hard_browser_action_without_opt_in": False,
                    "hard_browser_used_before_source_read": False,
                    "hard_browser_read_missing_fallback_reason": True,
                }
            },
        )
        is False
    )
    assert matrix.hard_safety_ok(profile="medium", summary={}) is True


def test_hard_profile_claims_axis_requires_records(tmp_path) -> None:
    matrix = _load_matrix_module()

    assert (
        matrix.hard_claims_ok(
            profile="hard",
            summary={
                "research_efficiency": {
                    "hard_claims_artifact_seen": True,
                    "claims_record_count": 0,
                }
            },
        )
        is False
    )
    assert (
        matrix.hard_claims_ok(
            profile="hard",
            summary={
                "research_efficiency": {
                    "hard_claims_artifact_seen": True,
                    "claims_record_count": 2,
                    "claims_verified_count": 2,
                    "claims_unsupported_count": 0,
                }
            },
        )
        is True
    )
    assert (
        matrix.hard_claims_ok(
            profile="hard",
            summary={
                "research_efficiency": {
                    "hard_claims_artifact_seen": True,
                    "claims_record_count": 2,
                    "claims_verified_count": 0,
                    "claims_unsupported_count": 0,
                }
            },
        )
        is False
    )


def test_hard_profile_claims_axis_rejects_non_ledger_urls(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    sources = artifact_dir / "research" / "sources.jsonl"
    claims = artifact_dir / "research" / "claims.jsonl"
    sources.parent.mkdir(parents=True)
    sources.write_text(
        '{"url":"https://example.com/a","status":"verified"}\n',
        encoding="utf-8",
    )
    claims.write_text(
        (
            '{"claim_id":"c1","status":"verified",'
            '"cited_urls":["https://example.com/missing"]}\n'
        ),
        encoding="utf-8",
    )

    assert (
        matrix.hard_claims_ok(
            profile="hard",
            artifact_dir=artifact_dir,
            summary={
                "research_efficiency": {
                    "hard_claims_artifact_seen": True,
                    "claims_record_count": 1,
                    "claims_verified_count": 1,
                    "claims_unsupported_count": 0,
                }
            },
        )
        is False
    )


def test_hard_profile_claims_axis_accepts_verified_ledger_urls(tmp_path) -> None:
    matrix = _load_matrix_module()
    artifact_dir = tmp_path / "run"
    sources = artifact_dir / "research" / "sources.jsonl"
    claims = artifact_dir / "research" / "claims.jsonl"
    sources.parent.mkdir(parents=True)
    sources.write_text(
        '{"url":"https://example.com/a","status":"verified"}\n',
        encoding="utf-8",
    )
    claims.write_text(
        (
            '{"claim_id":"c1","status":"verified",'
            '"cited_urls":["https://example.com/a"]}\n'
        ),
        encoding="utf-8",
    )

    assert (
        matrix.hard_claims_ok(
            profile="hard",
            artifact_dir=artifact_dir,
            summary={
                "research_efficiency": {
                    "hard_claims_artifact_seen": True,
                    "claims_record_count": 1,
                    "claims_verified_count": 1,
                    "claims_unsupported_count": 0,
                }
            },
        )
        is True
    )


def test_medium_acceptance_checks_subagent_synthesis_flags(tmp_path) -> None:
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
            "subagents": {
                "runs_completed": 1,
                "child_search_count": 1,
                "child_synthesis_pending": True,
            },
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "final_references_report_artifact": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["synthesis"] is False


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
                "final_references_report_artifact": True,
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
                "final_references_report_artifact": True,
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
                "final_references_report_artifact": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["synthesis"] is False


def test_medium_acceptance_requires_concise_report_handoff(tmp_path) -> None:
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
                "final_missing_report_reference": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["handoff"] is False


def test_medium_acceptance_uses_trace_handoff_completion_signal(tmp_path) -> None:
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
            "deep_research_artifact_handoff_complete": True,
            "artifacts": {
                "paths": ["research/report.md", "research/sources.jsonl"],
                "report_write_seen": True,
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "final_references_report_artifact": True,
                "long_final_after_report": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["handoff"] is True


def test_medium_evidence_split_requires_parent_synthesis_artifacts(tmp_path) -> None:
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
            },
            "subagents": {"child_count": 1},
            "research_efficiency": {
                "source_ledger_record_count": 1,
                "child_fetch_count": 1,
                "final_references_report_artifact": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["evidence_split"] is False


def test_medium_evidence_split_requires_child_evidence(tmp_path) -> None:
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
                "child_search_count": 0,
                "child_fetch_count": 0,
                "child_verified_read_count": 0,
                "final_references_report_artifact": True,
            },
            "llm": {"usage": {"total_tokens": 10}},
        },
    )

    assert acceptance["evidence_split"] is False


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
