"""Epic 043 C: partial compaction must not fold scaffolding into user intent."""

from __future__ import annotations

from agent_driver.context.compaction.partial import build_partial_compaction
from agent_driver.contracts.scaffolding import SCAFFOLDING_METADATA_KEY


def _msg(role: str, content: str, scaffolding: str | None = None) -> dict:
    row = {"role": role, "content": content}
    if scaffolding is not None:
        row["metadata"] = {SCAFFOLDING_METADATA_KEY: scaffolding}
    return row


def test_scaffolding_row_relabeled_as_runtime_in_summary() -> None:
    messages = [
        _msg("user", "REAL user request that must stay attributed to user"),
        _msg("assistant", "working on it"),
        _msg("user", "runtime nudge: call todo_write", scaffolding="todo_reminder"),
        # Tail that stays untouched (retain_recent_messages).
        *[_msg("assistant", f"turn {i}") for i in range(6)],
    ]
    out = build_partial_compaction(messages=messages, retain_recent_messages=6)
    summary = next(
        m["content"] for m in out.prompt_messages if m["role"] == "system"
    )
    # The genuine user turn keeps its user attribution...
    assert "- user: REAL user request" in summary
    # ...while the scaffolding nudge is attributed to runtime, never user.
    assert "- runtime: runtime nudge" in summary
    assert "- user: runtime nudge" not in summary
