"""The artifact pattern for subagent fan-out (coordination C5, deep-agent kernel).

On a wide fan-out, returning each child's full findings up the chat multiplies the
parent's context ~15× (Anthropic's multi-agent research). The *artifact pattern* is the
fix both Anthropic and LangChain Deep Agents converge on: a worker writes its findings to
the shared workspace and returns only a **light reference** (path + a short summary); a
later synthesis/verify phase reads the file when it actually needs the detail.

This is the domain-neutral SDK primitive. The runtime already had a narrow precedent
(deep-research draft capture) and the read-back tools (``artifact_read`` / ``file_write``);
here the SDK subagent path gets the same pattern generically:

    group = await run_subagent_group(parent, share_workspace(specs, ws), ...)
    arts = capture_group_artifacts(group, workspace_cwd=ws)
    # thread the compact refs — not the full answers — into the next phase
    brief = artifact_references(arts)

``share_workspace`` fixes the companion gap: an SDK child does not inherit the parent's
``workspace_cwd`` by default, so a later phase could not read an earlier phase's artifacts.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from agent_driver.contracts.enums import RunStatus
from agent_driver.sdk.group import SubagentGroupResult
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec

_WS = re.compile(r"\s+")
_UNSAFE = re.compile(r"[^0-9A-Za-z._-]+")


@dataclass(frozen=True, slots=True)
class SubagentArtifact:
    """A child's findings persisted to the shared workspace, plus a light reference."""

    agent_type: str
    path: str  # workspace-relative path the findings were written to
    summary: str  # short single-line head of the content
    char_count: int
    status: RunStatus

    def as_reference(self) -> str:
        """The compact string a downstream phase sees instead of the full answer."""
        return f"[{self.agent_type} → {self.path} · {self.char_count} chars] {self.summary}"


def _summarize(answer: str, *, summary_chars: int) -> str:
    flat = _WS.sub(" ", answer).strip()
    if len(flat) <= summary_chars:
        return flat
    return flat[: max(0, summary_chars - 1)].rstrip() + "…"


def _slug(agent_type: str) -> str:
    slug = _UNSAFE.sub("_", agent_type).strip("_")
    return slug or "worker"


def _capturable(result: SubagentResult | None, *, include_partial: bool) -> bool:
    if result is None or not (result.answer or "").strip():
        return False
    return include_partial or result.status == RunStatus.COMPLETED


def capture_subagent_artifact(
    result: SubagentResult | None,
    *,
    workspace_cwd: str | Path,
    subdir: str = "artifacts",
    filename: str | None = None,
    summary_chars: int = 280,
    include_partial: bool = False,
) -> SubagentArtifact | None:
    """Write one child's answer to the shared workspace; return a light reference.

    The answer is written to ``<workspace_cwd>/<subdir>/<filename>`` (``filename``
    defaults to ``<agent_type>.md``) and a :class:`SubagentArtifact` carrying the
    workspace-relative path plus a one-line summary is returned. A child with no answer
    text — or a non-``COMPLETED`` child when ``include_partial`` is false — yields
    ``None`` (nothing worth persisting).
    """
    if not _capturable(result, include_partial=include_partial):
        return None
    assert result is not None  # narrowed by _capturable
    answer = result.answer or ""
    name = filename or f"{_slug(result.agent_type)}.md"
    target_dir = Path(workspace_cwd) / subdir
    target_dir.mkdir(parents=True, exist_ok=True)
    (target_dir / name).write_text(answer, encoding="utf-8")
    rel = f"{subdir}/{name}" if subdir else name
    return SubagentArtifact(
        agent_type=result.agent_type,
        path=rel,
        summary=_summarize(answer, summary_chars=summary_chars),
        char_count=len(answer),
        status=result.status,
    )


def capture_group_artifacts(
    group: SubagentGroupResult,
    *,
    workspace_cwd: str | Path,
    subdir: str = "artifacts",
    summary_chars: int = 280,
    include_partial: bool = False,
) -> list[SubagentArtifact]:
    """Capture every eligible child of a group as an artifact (in spec order).

    Filenames are prefixed with the child's index (``00_<agent_type>.md``) so two
    workers of the same ``agent_type`` never overwrite each other. Non-eligible children
    (no answer, or non-``COMPLETED`` without ``include_partial``) are skipped.
    """
    artifacts: list[SubagentArtifact] = []
    for index, result in enumerate(group.results):
        if not _capturable(result, include_partial=include_partial):
            continue
        assert result is not None
        art = capture_subagent_artifact(
            result,
            workspace_cwd=workspace_cwd,
            subdir=subdir,
            filename=f"{index:02d}_{_slug(result.agent_type)}.md",
            summary_chars=summary_chars,
            include_partial=include_partial,
        )
        if art is not None:
            artifacts.append(art)
    return artifacts


def artifact_references(
    artifacts: Sequence[SubagentArtifact],
    *,
    header: str | None = None,
) -> str:
    """Render artifacts as a compact reference block for a downstream phase.

    This is the token-saving thread: instead of feeding a synthesis/verify phase the full
    concatenated findings, feed it these one-line references and let it ``artifact_read``
    the files it needs. Empty input yields ``""``.
    """
    if not artifacts:
        return ""
    lead = header or (
        "Findings from the previous phase were written to the workspace. "
        "Read a file for full detail:"
    )
    lines = "\n".join(f"- {art.as_reference()}" for art in artifacts)
    return f"{lead}\n{lines}"


def share_workspace(
    specs: Sequence[SubagentSpec],
    workspace_cwd: str | Path,
) -> list[SubagentSpec]:
    """Return copies of ``specs`` that inherit ``workspace_cwd`` (deep-agent sharing).

    SDK children do not inherit the parent's workspace by default; injecting
    ``workspace_cwd`` into each child's ``app_metadata`` lets a later phase read the
    artifacts an earlier phase wrote. Existing spec metadata is preserved.
    """
    cwd = str(workspace_cwd)
    return [spec.with_app_metadata(workspace_cwd=cwd) for spec in specs]


__all__ = [
    "SubagentArtifact",
    "artifact_references",
    "capture_group_artifacts",
    "capture_subagent_artifact",
    "share_workspace",
]
