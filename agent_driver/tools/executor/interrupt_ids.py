"""Unified interrupt / attempt identifier derivation (U2 — epic 050).

Both interrupt builders route through these so a host sees ONE consistent id
scheme instead of two: the tool-approval / gate path
(``policy_interrupt.build_tool_approval_interrupt``) previously minted
``int_{run_id}_{index}`` while the allow-path clarification / wait-for-event /
plan-approval interrupts (``allowed.py``) minted ``int_{tool_call_id or index}``.
The unified id is **run-scoped** (unique across runs) and **stable per logical
call** (prefers the harness-minted ``tool_call_id`` so the same id survives
gate → interrupt → approval/resume), falling back to the batch index.
"""

from __future__ import annotations


def build_interrupt_id(
    *, run_id: str | None, tool_call_id: str | None, index: int
) -> str:
    """Return a run-scoped interrupt id, stable per logical planned call.

    ``int_{run_id}_{tool_call_id}`` when a non-empty tool_call_id is present,
    else ``int_{run_id}_{index}``. ``run_id`` falls back to ``"runtime"`` when
    absent. Equal to the historical tool-approval scheme when no tool_call_id
    exists, so unstamped calls keep their previous id.
    """
    call_part = (
        str(tool_call_id).strip()
        if tool_call_id is not None and str(tool_call_id).strip()
        else str(index)
    )
    return f"int_{run_id or 'runtime'}_{call_part}"


def build_attempt_id(*, index: int, attempt_id: str | None = None) -> str:
    """Prefer an explicit run-supplied attempt id, else derive from batch index."""
    if attempt_id is not None and str(attempt_id).strip():
        return str(attempt_id)
    return f"attempt_{index}"


__all__ = ["build_attempt_id", "build_interrupt_id"]
