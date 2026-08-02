"""R3 (epic 059) — plan policy binding through checkpoint / resume / trace.

The U5 slice (``test_plan_integrity.py``) locked the harness-authored hash, EDIT
re-hash, and that a host binding survives into the in-memory ``approved_plan``.
This closes the parts the 0.2.0 handoff left open:

- the binding + plan identity reach the redaction-safe **trace projection** (the
  ``PLAN_APPROVED`` / ``PLAN_REJECTED`` runtime event a host reads), not only the
  live ``context.metadata``;
- they survive a **real checkpoint round-trip** (read back from the store, not a
  helper dict);
- the binding is **unforgeable** — it comes only from the resume command, so a
  pending-interrupt / model payload cannot inject one;
- an **EDIT** re-hashes the plan on the same trace path (a material revision is
  visible as a changed ``content_hash``).

Reuses the plan-approval harness from ``test_plan_integrity``.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.interrupts import ResumeCommand
from agent_driver.context.planning import plan_content_hash
from tests.runtime.test_plan_integrity import _pause_on_plan, _policy, _runner


def _plan_events(runner, run_id: str, event_type) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in runner._deps.event_log.list_for_run(run_id)
        if event.type == event_type
    ]


def _resume(run_id, interrupt_id, *, action=ResumeAction.APPROVE, **kw) -> AgentRunInput:
    return AgentRunInput(
        run_id=run_id,
        resume=ResumeCommand(interrupt_id=interrupt_id, action=action, **kw),
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=_policy(),
    )


@pytest.mark.asyncio
async def test_plan_approved_event_carries_binding() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_bind_trace")
    await runner.run(
        _resume(
            "run_bind_trace",
            paused.interrupt.interrupt_id,
            approved_by="operator-7",
            metadata={"plan_policy_binding": "policy-snapshot-42"},
        )
    )
    approved = _plan_events(runner, "run_bind_trace", RuntimeEventType.PLAN_APPROVED)
    assert len(approved) == 1, approved
    ev = approved[0]
    assert ev["policy_binding"] == "policy-snapshot-42"
    assert ev["approved_by"] == "operator-7"
    assert ev["plan_id"] is not None
    assert ev["content_hash"]  # plan identity present on the trace event


@pytest.mark.asyncio
async def test_binding_survives_real_checkpoint_readback() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_bind_ckpt")
    await runner.run(
        _resume(
            "run_bind_ckpt",
            paused.interrupt.interrupt_id,
            approved_by="op-1",
            metadata={"plan_policy_binding": {"snapshot": "s9", "scope": "run"}},
        )
    )
    # Read the binding back from the durable checkpoint store — not a helper dict.
    record = runner._deps.checkpoint_store.latest("run_bind_ckpt")
    assert record is not None
    approved_plan = record.state.metadata["approved_plan"]
    assert approved_plan["policy_binding"] == {"snapshot": "s9", "scope": "run"}
    assert approved_plan["approved_by"] == "op-1"


@pytest.mark.asyncio
async def test_binding_is_unforgeable_without_resume_metadata() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_bind_none")
    # Approve WITHOUT a resume binding — no binding may materialise from the
    # pending interrupt / model payload.
    await runner.run(_resume("run_bind_none", paused.interrupt.interrupt_id))
    approved = _plan_events(runner, "run_bind_none", RuntimeEventType.PLAN_APPROVED)
    assert len(approved) == 1
    assert "policy_binding" not in approved[0]
    record = runner._deps.checkpoint_store.latest("run_bind_none")
    assert "policy_binding" not in record.state.metadata["approved_plan"]


@pytest.mark.asyncio
async def test_edit_rehashes_on_trace_event() -> None:
    runner = _runner()
    paused = await _pause_on_plan(runner, "run_bind_edit")
    edited = "1. Inspect\n2. Write\n3. Verify\n4. DELETE everything"
    await runner.run(
        _resume(
            "run_bind_edit",
            paused.interrupt.interrupt_id,
            action=ResumeAction.EDIT,
            edited_tool_args={"content": edited},
        )
    )
    approved = _plan_events(runner, "run_bind_edit", RuntimeEventType.PLAN_APPROVED)
    assert len(approved) == 1
    # The trace event's content_hash is the harness hash of the EDITED plan — a
    # material revision the host can require re-approval against.
    assert approved[0]["content_hash"] == plan_content_hash(edited)
