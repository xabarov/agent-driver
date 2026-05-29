"""Plan checklist rendering for chat TUI."""

from __future__ import annotations

from typing import Any

STATUS_ICONS: dict[str, str] = {
    "completed": "✓",
    "in_progress": "■",
    "pending": "□",
    "cancelled": "⊘",
}


def format_plan_panel(snapshot: dict[str, Any]) -> str:
    """Render plan snapshot as plain-text checklist lines."""
    todos = snapshot.get("todos")
    if not isinstance(todos, list) or not todos:
        return ""
    completed = int(snapshot.get("completed") or 0)
    total = int(snapshot.get("total") or len(todos))
    header = f"Plan · {completed}/{total} done"
    in_progress_index = snapshot.get("in_progress_index")
    if isinstance(in_progress_index, int) and total > 0:
        header += f" · step {in_progress_index}/{total}"
    plan_title = snapshot.get("plan_title")
    if isinstance(plan_title, str) and plan_title.strip():
        short = plan_title.strip()
        if len(short) > 48:
            short = f"{short[:45]}..."
        header += f" · active: {short}"
    lines = [header]
    for row in todos:
        if not isinstance(row, dict):
            continue
        status = str(row.get("status") or "pending")
        icon = STATUS_ICONS.get(status, "□")
        content = str(row.get("content") or "").strip().replace("\n", " ")
        if len(content) > 72:
            content = f"{content[:69]}..."
        lines.append(f"  {icon} {content}")
    return "\n".join(lines)


def plan_progress_footer(snapshot: dict[str, Any]) -> tuple[str, str | None]:
    """Return (progress_label, current_step) for prompt footer."""
    completed = int(snapshot.get("completed") or 0)
    total = int(snapshot.get("total") or 0)
    progress = f"plan {completed}/{total}" if total > 0 else ""
    current: str | None = None
    plan_title = snapshot.get("plan_title")
    if isinstance(plan_title, str) and plan_title.strip():
        current = plan_title.strip()[:48]
    else:
        in_progress_id = snapshot.get("in_progress_id")
        todos = snapshot.get("todos")
        if isinstance(in_progress_id, str) and isinstance(todos, list):
            for row in todos:
                if isinstance(row, dict) and row.get("id") == in_progress_id:
                    text = str(row.get("content") or "").strip()
                    if text:
                        current = text[:48]
                    break
    return progress, current


__all__ = ["STATUS_ICONS", "format_plan_panel", "plan_progress_footer"]
