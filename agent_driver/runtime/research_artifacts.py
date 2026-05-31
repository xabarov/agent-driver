"""Durable Deep Research artifact helpers."""

from __future__ import annotations

from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING, Any

from agent_driver.runtime.research_evidence import RESEARCH_DEPTH_DEEP_PARALLEL

if TYPE_CHECKING:
    from agent_driver.runtime.single_agent.types import RunContext


DEFAULT_INLINE_ANSWER_MAX_CHARS = 6_000
REPORT_RELATIVE_PATH = "research/report.md"


def deep_research_artifact_mode(context: "RunContext") -> bool:
    """Return whether the current run should use durable research artifacts."""
    metadata = context.run_input.tool_policy.metadata
    mode = metadata.get("deep_research_mode")
    if isinstance(mode, dict) and mode.get("enabled") is True:
        return True
    task_contract = metadata.get("task_contract")
    return (
        isinstance(task_contract, dict)
        and task_contract.get("research_depth") == RESEARCH_DEPTH_DEEP_PARALLEL
    )


def deep_research_report_artifact_exists(context: "RunContext") -> bool:
    """Return whether this run already has a non-empty report artifact."""
    payload = context.metadata.get("deep_research_artifacts")
    if isinstance(payload, dict) and payload.get("report_exists") is True:
        return True
    report = _report_path(context)
    return report is not None and report.is_file() and report.stat().st_size > 0


def ensure_deep_research_report_artifact_metadata(
    context: "RunContext",
) -> dict[str, Any] | None:
    """Record metadata for an existing report artifact if Deep Research is active."""
    payload = context.metadata.get("deep_research_artifacts")
    if isinstance(payload, dict) and payload.get("report_exists") is True:
        return payload
    if not deep_research_artifact_mode(context):
        return None
    report = _report_path(context)
    if report is None or not report.is_file() or report.stat().st_size <= 0:
        return None
    return _record_report_metadata(
        context,
        report=report,
        captured=False,
        text_chars=0,
        reason="existing_report",
    )


def maybe_capture_deep_research_draft(
    context: "RunContext",
    text: str,
) -> dict[str, Any] | None:
    """Persist a long inline Deep Research draft to ``research/report.md``.

    This is a write-through guard for the expensive loop where the model writes
    a large report in chat, final-readiness fails on stale todos, and the next
    LLM turn would otherwise receive and rewrite the full draft.
    """
    if not deep_research_artifact_mode(context):
        return None
    if not isinstance(text, str) or not text.strip():
        return None
    max_chars = _inline_answer_max_chars(context)
    if len(text) < max_chars:
        return None
    report = _report_path(context)
    if report is None:
        return None
    if report.exists() and report.stat().st_size > 0:
        _record_report_metadata(
            context,
            report=report,
            captured=False,
            text_chars=len(text),
            reason="existing_report",
        )
        return None
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(text, encoding="utf-8")
    return _record_report_metadata(
        context,
        report=report,
        captured=True,
        text_chars=len(text),
        reason="long_inline_answer",
    )


def captured_draft_protocol_text(payload: dict[str, Any]) -> str:
    """Return compact assistant text for continuation prompts after capture."""
    path = str(payload.get("report_path") or REPORT_RELATIVE_PATH)
    chars = int(payload.get("captured_text_chars") or 0)
    return (
        f"[Deep Research draft captured to {path}; {chars} chars. "
        "Continue from the artifact instead of rewriting the report in chat.]"
    )


def deep_research_artifact_repair_hint(context: "RunContext") -> str | None:
    """Return compact repair hint when a report artifact is available."""
    if not deep_research_report_artifact_exists(context):
        return None
    payload = context.metadata.get("deep_research_artifacts")
    path = REPORT_RELATIVE_PATH
    if isinstance(payload, dict) and isinstance(payload.get("report_path"), str):
        path = str(payload["report_path"])
    return (
        f"A durable draft exists at {path}. Do not rewrite the full report in "
        "chat. Use todo_write for stale checklist state, or read_file plus "
        "file_edit/file_write only for targeted artifact changes."
    )


def _report_path(context: "RunContext") -> Path | None:
    workspace = context.metadata.get("workspace_cwd")
    if not isinstance(workspace, str) or not workspace.strip():
        return None
    root = Path(workspace).expanduser().resolve()
    report = (root / REPORT_RELATIVE_PATH).resolve()
    try:
        report.relative_to(root)
    except ValueError:
        return None
    return report


def _inline_answer_max_chars(context: "RunContext") -> int:
    raw = (
        context.run_input.app_metadata.get("deep_research_inline_answer_max_chars")
        if isinstance(context.run_input.app_metadata, dict)
        else None
    )
    if raw is None:
        raw = context.metadata.get("deep_research_inline_answer_max_chars")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_INLINE_ANSWER_MAX_CHARS
    return max(1_000, value)


def _record_report_metadata(
    context: "RunContext",
    *,
    report: Path,
    captured: bool,
    text_chars: int,
    reason: str,
) -> dict[str, Any]:
    size = report.stat().st_size if report.exists() else 0
    digest = sha256(report.read_bytes()).hexdigest() if report.exists() else ""
    previous = context.metadata.get("deep_research_artifacts")
    prior_count = (
        int(previous.get("captured_long_answers", 0))
        if isinstance(previous, dict)
        else 0
    )
    payload: dict[str, Any] = {
        "workspace_root": str(report.parents[1]),
        "report_path": REPORT_RELATIVE_PATH,
        "report_absolute_path": str(report),
        "report_exists": report.exists(),
        "report_size_bytes": size,
        "report_sha256": digest,
        "captured_long_answers": prior_count + (1 if captured else 0),
        "captured_text_chars": text_chars,
        "last_update_kind": "capture" if captured else "observed",
        "last_update_reason": reason,
    }
    context.metadata["deep_research_artifacts"] = payload
    return payload


__all__ = [
    "DEFAULT_INLINE_ANSWER_MAX_CHARS",
    "REPORT_RELATIVE_PATH",
    "captured_draft_protocol_text",
    "deep_research_artifact_mode",
    "deep_research_artifact_repair_hint",
    "deep_research_report_artifact_exists",
    "ensure_deep_research_report_artifact_metadata",
    "maybe_capture_deep_research_draft",
]
