"""Per-session agent workspace paths for chat-demo."""

from __future__ import annotations

from pathlib import Path

from agent_driver.cli.sessions import SessionStore

from app.config import Settings
from app.schemas.meta import WorkspaceStatusView

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


def _count_workspace_files(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(1 for item in path.rglob("*") if item.is_file())


def workspace_status(
    settings: Settings,
    session_id: str | None,
    *,
    create: bool = False,
) -> WorkspaceStatusView:
    """Return session workspace metadata without exposing unrelated server files."""
    workspace_root = resolved_workspace_root(settings)
    session_workspace: Path | None = None
    if isinstance(session_id, str) and session_id.strip():
        session_workspace = (
            resolve_session_workspace(settings, session_id.strip())
            if create
            else (workspace_root / session_id.strip()).resolve()
        )
    root = session_workspace or workspace_root
    return WorkspaceStatusView(
        root=str(root),
        sessionId=session_id.strip() if isinstance(session_id, str) and session_id.strip() else None,
        exists=root.is_dir(),
        fileCount=_count_workspace_files(root),
        sampleAvailable=session_workspace is not None,
    )


_SAMPLE_FILES: dict[str, str] = {
    "README.md": """# Vision Transformer Demo Project

This sample workspace gives the web demo real files for read_file, glob_search,
and grep_search. It is intentionally small and isolated to this chat session.
""",
    "src/vision_transformer.py": '''"""Tiny Vision Transformer shape sketch for demo search."""


def patchify(image_size: int, patch_size: int) -> int:
    """Return the number of square patches for one image."""
    patches_per_side = image_size // patch_size
    return patches_per_side * patches_per_side


class VisionTransformer:
    def __init__(self, image_size: int = 224, patch_size: int = 16) -> None:
        self.image_size = image_size
        self.patch_size = patch_size

    def token_count(self) -> int:
        return patchify(self.image_size, self.patch_size) + 1
''',
    "notes/transformers.md": """# Transformers In Computer Vision

- ViT splits an image into patches and treats them as tokens.
- DETR applies transformer decoders to object detection.
- Attention helps connect distant visual regions without convolution-only bias.
""",
}


def import_sample_project(settings: Settings, session_id: str) -> list[str]:
    """Copy a tiny read-only demo project into the session workspace."""
    workspace = resolve_session_workspace(settings, session_id)
    written: list[str] = []
    for relative, content in _SAMPLE_FILES.items():
        target = workspace / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
        written.append(relative)
    return written


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
        "llm_stream_idle_timeout_seconds": settings.llm_stream_idle_timeout_seconds,
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
    merged["llm_stream_idle_timeout_seconds"] = settings.llm_stream_idle_timeout_seconds
    merged.setdefault("chat_mode", True)
    workspace = merged.get("workspace_cwd")
    if isinstance(workspace, str) and workspace.strip():
        return merged
    session_id = find_session_id_for_run(session_store, run_id)
    if session_id:
        merged["workspace_cwd"] = str(resolve_session_workspace(settings, session_id))
    return merged
