"""The artifact pattern for subagent fan-out (coordination C5)."""

from __future__ import annotations

from pathlib import Path

from agent_driver.contracts.enums import RunStatus
from agent_driver.sdk import (
    SubagentArtifact,
    SubagentSpec,
    artifact_references,
    capture_group_artifacts,
    capture_subagent_artifact,
    share_workspace,
)
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.contracts.enums import SubagentJoinPolicy
from agent_driver.sdk.subagent import SubagentResult


def _result(
    agent_type: str, status: RunStatus, answer: str | None
) -> SubagentResult:
    return SubagentResult(
        child_run_id=f"c-{agent_type}",
        parent_run_id="p",
        agent_type=agent_type,
        status=status,
        terminal_reason=None,
        answer=answer,
        structured_output=None,
        tool_trace=(),
        usage=None,
        raw_output=None,
    )


def _group(*results: SubagentResult) -> SubagentGroupResult:
    return SubagentGroupResult(
        results=tuple(results),
        errors=tuple(None for _ in results),
        join_policy=SubagentJoinPolicy.WAIT_ALL,
        satisfied=True,
    )


def test_capture_writes_answer_and_returns_light_ref(tmp_path: Path) -> None:
    res = _result("researcher", RunStatus.COMPLETED, "The pricing analysis is X.\nDetail…")
    art = capture_subagent_artifact(res, workspace_cwd=tmp_path)
    assert isinstance(art, SubagentArtifact)
    assert art.path == "artifacts/researcher.md"
    written = (tmp_path / "artifacts" / "researcher.md").read_text(encoding="utf-8")
    assert written == "The pricing analysis is X.\nDetail…"
    assert art.char_count == len(written)
    # summary is a single line (newlines collapsed), reference is compact
    assert "\n" not in art.summary
    assert "researcher.md" in art.as_reference()
    assert str(art.char_count) in art.as_reference()


def test_capture_summary_truncates_long_answer(tmp_path: Path) -> None:
    res = _result("w", RunStatus.COMPLETED, "x" * 1000)
    art = capture_subagent_artifact(res, workspace_cwd=tmp_path, summary_chars=50)
    assert art is not None
    assert len(art.summary) == 50 and art.summary.endswith("…")
    assert art.char_count == 1000  # full answer still persisted


def test_capture_skips_empty_answer(tmp_path: Path) -> None:
    assert capture_subagent_artifact(
        _result("w", RunStatus.COMPLETED, "   "), workspace_cwd=tmp_path
    ) is None
    assert capture_subagent_artifact(None, workspace_cwd=tmp_path) is None


def test_capture_skips_non_completed_unless_include_partial(tmp_path: Path) -> None:
    res = _result("w", RunStatus.FAILED, "partial findings")
    assert capture_subagent_artifact(res, workspace_cwd=tmp_path) is None
    art = capture_subagent_artifact(res, workspace_cwd=tmp_path, include_partial=True)
    assert art is not None and art.status == RunStatus.FAILED
    assert (tmp_path / "artifacts" / "w.md").exists()


def test_capture_group_indexes_filenames_to_avoid_collision(tmp_path: Path) -> None:
    group = _group(
        _result("dup", RunStatus.COMPLETED, "first"),
        _result("dup", RunStatus.COMPLETED, "second"),
        _result("dup", RunStatus.FAILED, "third-partial"),
    )
    arts = capture_group_artifacts(group, workspace_cwd=tmp_path)
    # two COMPLETED captured, the FAILED one skipped; no overwrite
    assert [a.path for a in arts] == ["artifacts/00_dup.md", "artifacts/01_dup.md"]
    assert (tmp_path / "artifacts" / "00_dup.md").read_text() == "first"
    assert (tmp_path / "artifacts" / "01_dup.md").read_text() == "second"


def test_capture_group_include_partial(tmp_path: Path) -> None:
    group = _group(
        _result("a", RunStatus.COMPLETED, "done"),
        _result("b", RunStatus.FAILED, "salvage"),
    )
    arts = capture_group_artifacts(group, workspace_cwd=tmp_path, include_partial=True)
    assert [a.path for a in arts] == ["artifacts/00_a.md", "artifacts/01_b.md"]


def test_artifact_references_is_compact_and_lists_paths(tmp_path: Path) -> None:
    group = _group(
        _result("alpha", RunStatus.COMPLETED, "A" * 500),
        _result("beta", RunStatus.COMPLETED, "B" * 500),
    )
    arts = capture_group_artifacts(group, workspace_cwd=tmp_path)
    block = artifact_references(arts)
    assert "00_alpha.md" in block and "01_beta.md" in block
    # the block is far smaller than the raw answers it stands in for
    assert len(block) < 1000
    assert artifact_references([]) == ""


def test_artifact_references_custom_header(tmp_path: Path) -> None:
    res = _result("w", RunStatus.COMPLETED, "hello")
    art = capture_subagent_artifact(res, workspace_cwd=tmp_path)
    assert art is not None
    block = artifact_references([art], header="Prior research:")
    assert block.startswith("Prior research:\n- ")


def test_share_workspace_injects_cwd_preserving_metadata(tmp_path: Path) -> None:
    specs = [
        SubagentSpec(agent_type="a", prompt="pa", app_metadata={"keep": 1}),
        SubagentSpec(agent_type="b", prompt="pb"),
    ]
    shared = share_workspace(specs, tmp_path)
    assert all(s.app_metadata["workspace_cwd"] == str(tmp_path) for s in shared)
    assert shared[0].app_metadata["keep"] == 1  # existing metadata preserved
    # originals untouched (frozen copy semantics)
    assert "workspace_cwd" not in specs[0].app_metadata
    assert shared[0].prompt == "pa" and shared[1].agent_type == "b"


def test_slug_sanitizes_agent_type_for_filename(tmp_path: Path) -> None:
    res = _result("weird/type name!", RunStatus.COMPLETED, "x")
    art = capture_subagent_artifact(res, workspace_cwd=tmp_path)
    assert art is not None
    # path is filesystem-safe and actually exists
    assert (tmp_path / art.path).exists()
    assert "/" not in art.path.split("/")[-1].replace(".md", "")
