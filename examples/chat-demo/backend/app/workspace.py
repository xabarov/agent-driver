"""Per-session agent workspace paths for chat-demo."""

from __future__ import annotations

from pathlib import Path

from agent_driver.cli.sessions import SessionStore

from app.config import Settings

_CHAT_DEMO_ROOT = Path(__file__).resolve().parents[2]


def resolved_workspace_root(settings: Settings) -> Path:
    """Return absolute workspace root directory."""
    root = settings.workspace_root
    if root.is_absolute():
        return root.resolve()
    return (_CHAT_DEMO_ROOT / root).resolve()


def resolve_session_workspace(settings: Settings, session_id: str) -> Path:
    """Ensure and return per-session sandbox directory."""
    session_dir = resolved_workspace_root(settings) / session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir.resolve()


def find_session_id_for_run(store: SessionStore, run_id: str) -> str | None:
    """Find session_id that owns the given run_id."""
    for record in store.list_sessions():
        if run_id in record.run_ids:
            return record.session_id
    return None


def build_chat_app_metadata(settings: Settings, session_id: str) -> dict[str, object]:
    """Metadata passed into AgentRunInput for chat-demo runs."""
    return {
        "stream_poll_interval_ms": settings.stream_poll_interval_ms,
        "chat_mode": True,
        "workspace_cwd": str(resolve_session_workspace(settings, session_id)),
    }


def merge_resume_app_metadata(
    settings: Settings,
    *,
    base_metadata: dict[str, object] | None,
    run_id: str,
    session_store: SessionStore,
) -> dict[str, object]:
    """Preserve workspace from checkpoint or resolve from session store."""
    merged = dict(base_metadata or {})
    merged["stream_poll_interval_ms"] = settings.stream_poll_interval_ms
    merged.setdefault("chat_mode", True)
    workspace = merged.get("workspace_cwd")
    if isinstance(workspace, str) and workspace.strip():
        return merged
    session_id = find_session_id_for_run(session_store, run_id)
    if session_id:
        merged["workspace_cwd"] = str(resolve_session_workspace(settings, session_id))
    return merged
