"""Tests for per-session agent workspace configuration."""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_driver.tools.builtin.filesystem._paths import resolve_writable_path
from agent_driver.tools.context import workspace_cwd_scope

from app.deps import get_settings, reset_dependency_caches
from app.workspace import (
    build_chat_app_metadata,
    find_session_id_for_run,
    import_sample_project,
    list_workspace_artifacts,
    merge_resume_app_metadata,
    preview_workspace_artifact,
    resolve_session_workspace,
    resolved_workspace_root,
    workspace_status,
)


@pytest.fixture()
def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("CHAT_DEMO_WORKSPACE_ROOT", str(tmp_path / "workspace"))
    monkeypatch.setenv("CHAT_DEMO_SESSIONS_PATH", str(tmp_path / "sessions.json"))
    reset_dependency_caches()
    return get_settings()


def test_resolved_workspace_root_relative_to_chat_demo(settings, tmp_path) -> None:
    root = resolved_workspace_root(settings)
    assert root == (tmp_path / "workspace").resolve()


def test_resolve_session_workspace_creates_directory(settings) -> None:
    path = resolve_session_workspace(settings, "session_abc")
    assert path.is_dir()
    assert path.name == "session_abc"
    assert path.parent == resolved_workspace_root(settings)


def test_build_chat_app_metadata_includes_workspace_and_chat_mode(settings) -> None:
    meta = build_chat_app_metadata(settings, "session_xyz")
    assert meta["chat_mode"] is True
    assert meta["session_id"] == "session_xyz"
    assert meta["stream_poll_interval_ms"] == settings.stream_poll_interval_ms
    workspace = Path(str(meta["workspace_cwd"]))
    assert workspace == resolve_session_workspace(settings, "session_xyz")


def test_workspace_status_counts_session_files(settings) -> None:
    workspace = resolve_session_workspace(settings, "session_status")
    (workspace / "notes.txt").write_text("hello", encoding="utf-8")
    status = workspace_status(settings, "session_status")
    assert status.session_id == "session_status"
    assert status.exists is True
    assert status.file_count == 1


def test_import_sample_project_creates_demo_files(settings) -> None:
    files = import_sample_project(settings, "session_sample")
    workspace = resolve_session_workspace(settings, "session_sample")
    assert "README.md" in files
    assert (workspace / "src" / "vision_transformer.py").is_file()


def test_workspace_artifact_index_and_preview(settings) -> None:
    workspace = resolve_session_workspace(settings, "session_artifacts")
    report = workspace / "research" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("hello report", encoding="utf-8")

    artifacts = list_workspace_artifacts(settings, "session_artifacts")
    artifact, content, truncated = preview_workspace_artifact(
        settings,
        "session_artifacts",
        "research/report.md",
    )

    assert [item.path for item in artifacts] == ["research/report.md"]
    assert artifacts[0].kind == "report"
    assert artifact.path == "research/report.md"
    assert content == "hello report"
    assert truncated is False


def test_workspace_artifact_preview_rejects_escape(settings) -> None:
    with pytest.raises(ValueError, match="outside workspace"):
        preview_workspace_artifact(settings, "session_artifacts", "../secret.txt")


def test_find_session_id_for_run(settings) -> None:
    from agent_driver.cli.sessions import SessionStore

    store = SessionStore(path=settings.sessions_path)
    store.upsert(
        session_id="session_1",
        thread_id="thread_1",
        run_ids=["run_a", "run_b"],
        transcript=[("user", "hi")],
    )
    assert find_session_id_for_run(store, "run_b") == "session_1"
    assert find_session_id_for_run(store, "run_missing") is None


def test_merge_resume_app_metadata_fills_missing_workspace(settings) -> None:
    from agent_driver.cli.sessions import SessionStore

    store = SessionStore(path=settings.sessions_path)
    store.upsert(
        session_id="session_resume",
        thread_id="thread_1",
        run_ids=["run_resume"],
        transcript=[],
    )
    merged = merge_resume_app_metadata(
        settings,
        base_metadata={"stream_poll_interval_ms": 99},
        run_id="run_resume",
        session_store=store,
    )
    assert merged["chat_mode"] is True
    assert Path(str(merged["workspace_cwd"])) == resolve_session_workspace(
        settings, "session_resume"
    )


def test_path_jail_rejects_outside_workspace(tmp_path) -> None:
    jail = tmp_path / "jail"
    jail.mkdir()
    outside = tmp_path / "outside.txt"
    with workspace_cwd_scope(jail):
        with pytest.raises(ValueError, match="path outside workspace"):
            resolve_writable_path(str(outside), create_parent=False)
