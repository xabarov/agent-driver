"""Per-session agent workspace paths for chat-demo."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
import re
from typing import NamedTuple

from app.config import Settings
from app.schemas.meta import WorkspaceStatusView

from agent_driver.cli.sessions import SessionStore

_CHAT_DEMO_ROOT = Path(__file__).resolve().parents[2]
_ARTIFACT_DIRS = ("research", "tool-results")
_PREVIEW_MAX_BYTES = 200_000


class WorkspaceArtifact(NamedTuple):
    """One indexed workspace artifact."""

    path: str
    kind: str
    size_bytes: int
    modified_at: str


def resolved_workspace_root(settings: Settings) -> Path:
    """Return absolute workspace root directory."""
    root = settings.workspace_root
    if root.is_absolute():
        return root.resolve()
    return (_CHAT_DEMO_ROOT / root).resolve()


def resolve_session_workspace(settings: Settings, session_id: str) -> Path:
    """Ensure and return per-session sandbox directory."""
    workspace_root = resolved_workspace_root(settings)
    session_dir = (workspace_root / session_id).resolve()
    try:
        session_dir.relative_to(workspace_root)
    except ValueError as exc:
        raise ValueError("session_id resolves outside workspace root") from exc
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


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
        sessionId=(
            session_id.strip()
            if isinstance(session_id, str) and session_id.strip()
            else None
        ),
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


def list_workspace_artifacts(
    settings: Settings,
    session_id: str,
) -> list[WorkspaceArtifact]:
    """Return stable artifact index for one session workspace."""
    workspace = resolve_session_workspace(settings, session_id)
    artifacts: list[WorkspaceArtifact] = []
    for dirname in _ARTIFACT_DIRS:
        root = workspace / dirname
        if not root.is_dir():
            continue
        for item in sorted(root.rglob("*")):
            if not item.is_file():
                continue
            relative = item.relative_to(workspace).as_posix()
            stat = item.stat()
            artifacts.append(
                WorkspaceArtifact(
                    path=relative,
                    kind=_artifact_kind(relative),
                    size_bytes=stat.st_size,
                    modified_at=datetime.fromtimestamp(
                        stat.st_mtime, tz=UTC
                    ).isoformat(),
                )
            )
    return artifacts


def preview_workspace_artifact(
    settings: Settings,
    session_id: str,
    artifact_path: str,
    *,
    max_bytes: int = _PREVIEW_MAX_BYTES,
) -> tuple[WorkspaceArtifact, str, bool]:
    """Return bounded UTF-8 preview for one artifact under session workspace."""
    workspace = resolve_session_workspace(settings, session_id)
    target = (workspace / artifact_path).resolve()
    try:
        relative = target.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError("artifact path outside workspace") from exc
    if not _is_artifact_relative_path(relative):
        raise ValueError("artifact path is not a known artifact")
    if not target.is_file():
        raise FileNotFoundError(relative)
    limit = max(1, int(max_bytes))
    data = target.read_bytes()
    truncated = len(data) > limit
    preview = data[:limit].decode("utf-8", errors="replace")
    stat = target.stat()
    artifact = WorkspaceArtifact(
        path=relative,
        kind=_artifact_kind(relative),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    )
    return artifact, preview, truncated


def read_workspace_artifact(
    settings: Settings,
    session_id: str,
    artifact_path: str,
    *,
    max_bytes: int = 2_000_000,
) -> tuple[WorkspaceArtifact, bytes]:
    """Return raw artifact bytes under the validated session workspace."""
    workspace = resolve_session_workspace(settings, session_id)
    target = (workspace / artifact_path).resolve()
    try:
        relative = target.relative_to(workspace).as_posix()
    except ValueError as exc:
        raise ValueError("artifact path outside workspace") from exc
    if not _is_artifact_relative_path(relative):
        raise ValueError("artifact path is not a known artifact")
    if not target.is_file():
        raise FileNotFoundError(relative)
    stat = target.stat()
    if stat.st_size > max_bytes:
        raise ValueError("artifact is too large to download")
    artifact = WorkspaceArtifact(
        path=relative,
        kind=_artifact_kind(relative),
        size_bytes=stat.st_size,
        modified_at=datetime.fromtimestamp(stat.st_mtime, tz=UTC).isoformat(),
    )
    return artifact, target.read_bytes()


def render_markdown_artifact_pdf(
    markdown: bytes, *, title: str = "Research Report"
) -> bytes:
    """Render a small text-only PDF for Markdown report downloads.

    This intentionally avoids shelling out to a browser or depending on a local
    PDF toolchain. It is not a typography engine; it provides a durable fallback
    export when raw Markdown is already available.
    """
    text = markdown.decode("utf-8", errors="replace")
    lines = _markdown_to_pdf_lines(text, title=title)
    pages = [lines[index : index + 42] for index in range(0, len(lines), 42)] or [[]]
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids ["
        + b" ".join(
            f"{3 + index * 2} 0 R".encode("ascii") for index in range(len(pages))
        )
        + f"] /Count {len(pages)} >>".encode("ascii"),
    ]
    for index, page_lines in enumerate(pages):
        page_object_id = 3 + index * 2
        content_object_id = page_object_id + 1
        stream = _pdf_text_stream(page_lines)
        objects.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Resources << /Font << /F1 << /Type /Font /Subtype /Type1 "
                f"/BaseFont /Helvetica >> >> >> /Contents {content_object_id} 0 R >>"
            ).encode("ascii")
        )
        objects.append(
            b"<< /Length "
            + str(len(stream)).encode("ascii")
            + b" >>\nstream\n"
            + stream
            + b"\nendstream"
        )
    return _pdf_document(objects)


def _is_artifact_relative_path(path: str) -> bool:
    return any(path.startswith(f"{dirname}/") for dirname in _ARTIFACT_DIRS)


def _artifact_kind(path: str) -> str:
    if path == "research/report.md":
        return "report"
    if path.startswith("research/"):
        return "research"
    if path.startswith("tool-results/"):
        return "tool_result"
    return "file"


def _markdown_to_pdf_lines(text: str, *, title: str) -> list[str]:
    rows = [title, ""]
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            rows.append("")
            continue
        line = re.sub(r"^#{1,6}\s*", "", line)
        line = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1 (\2)", line)
        line = re.sub(r"[*_`]+", "", line)
        rows.extend(_wrap_pdf_line(line, width=92))
    return rows


def _wrap_pdf_line(line: str, *, width: int) -> list[str]:
    words = line.split()
    if not words:
        return [""]
    rows: list[str] = []
    current = words[0]
    for word in words[1:]:
        if len(current) + 1 + len(word) > width:
            rows.append(current)
            current = word
        else:
            current = f"{current} {word}"
    rows.append(current)
    return rows


def _pdf_text_stream(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 10 Tf", "50 750 Td", "14 TL"]
    for index, line in enumerate(lines):
        if index:
            commands.append("T*")
        commands.append(f"({_pdf_escape(line)}) Tj")
    commands.append("ET")
    return "\n".join(commands).encode("latin-1", errors="replace")


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def _pdf_document(objects: list[bytes]) -> bytes:
    chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for index, payload in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii") + payload + b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    chunks.append(
        (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_offset}\n%%EOF\n"
        ).encode("ascii")
    )
    return b"".join(chunks)


def find_session_id_for_run(store: SessionStore, run_id: str) -> str | None:
    """Find session_id that owns the given run_id."""
    for record in store.list_sessions():
        if run_id in record.run_ids:
            return record.session_id
    return None


def build_chat_app_metadata(
    settings: Settings,
    session_id: str,
    *,
    scenario_id: str | None = None,
    research_metadata: dict[str, object] | None = None,
) -> dict[str, object]:
    """Metadata passed into AgentRunInput for chat-demo runs."""
    metadata: dict[str, object] = {
        "stream_poll_interval_ms": settings.stream_poll_interval_ms,
        "llm_stream_idle_timeout_seconds": settings.llm_stream_idle_timeout_seconds,
        "chat_mode": True,
        "session_id": session_id,
        "workspace_cwd": str(resolve_session_workspace(settings, session_id)),
    }
    if research_metadata:
        metadata.update(research_metadata)
    if isinstance(scenario_id, str) and scenario_id.strip():
        metadata["scenario_id"] = scenario_id.strip()
    return metadata


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
