"""U4 fencing enforce (epic 052) — stamp results + drop superseded stragglers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.enums import RuntimeEventType, ToolPolicyDecision
from agent_driver.contracts.tools import ToolCall, ToolResultEnvelope
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
)
from agent_driver.runtime.single_agent.fencing import (
    RESERVED_ATTEMPT_EPOCH_KEY,
    attempt_epoch_of,
)


def _runner():
    return FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
    )


def _env(name: str, epoch: int | None):
    meta = {RESERVED_ATTEMPT_EPOCH_KEY: epoch} if epoch is not None else {}
    return ToolResultEnvelope(
        call=ToolCall(tool_name=name, tool_call_id=f"c_{name}"),
        decision=ToolPolicyDecision.ALLOW,
        metadata=meta,
    )


def _ctx(epoch: int):
    return SimpleNamespace(attempt_epoch=epoch, run_id="r1", attempt_id="a1")


def test_fresh_result_stamped_stale_dropped() -> None:
    runner = _runner()
    fresh = _env("fresh", None)  # unstamped, produced this attempt
    stale = _env("stale", 1)  # straggler from a superseded attempt
    kept = runner._fence_and_stamp_envelopes(_ctx(2), [stale, fresh])
    names = [e.call.tool_name for e in kept]
    assert names == ["fresh"]  # stale dropped
    assert attempt_epoch_of(kept[0].metadata) == 2  # fresh stamped current
    events = runner._deps.event_log.list_for_run("r1")
    fenced = [e for e in events if e.type == RuntimeEventType.RESULT_FENCED]
    assert len(fenced) == 1
    assert fenced[0].payload["tool_name"] == "stale"


def test_fresh_run_epoch_zero_is_noop() -> None:
    runner = _runner()
    env = _env("t", None)
    kept = runner._fence_and_stamp_envelopes(_ctx(0), [env])
    assert len(kept) == 1
    # epoch 0 → no stamp, byte-identical metadata, no events.
    assert attempt_epoch_of(kept[0].metadata) is None
    assert runner._deps.event_log.list_for_run("r1") == []


def test_same_epoch_result_kept() -> None:
    runner = _runner()
    env = _env("t", 2)  # already stamped current
    kept = runner._fence_and_stamp_envelopes(_ctx(2), [env])
    assert len(kept) == 1 and attempt_epoch_of(kept[0].metadata) == 2
    assert runner._deps.event_log.list_for_run("r1") == []
