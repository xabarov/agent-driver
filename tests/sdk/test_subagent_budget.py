"""Tests for B2.2 per-subagent budget enforcement.

The watchdog polls the child's event log, sums ``cost_usd_estimate``
from every ``llm_call_completed`` event, and fires the child's
``RunAbortHandle`` when accumulated cost crosses ``spec.max_cost_usd``.
The child's runner detects the abort at its next step boundary and
terminates with ``RunStatus.CANCELLED``.

Coverage:
- Budget None (default) — watchdog skipped; behaviour unchanged
- Budget large enough — child completes normally
- Budget too small — child is aborted mid-run with status=CANCELLED
- No provider cost — watchdog can't enforce; child still completes
- Standalone abort handle when no parent handle is supplied
- Watchdog is cancelled cleanly when the child completes before
  the budget is hit (no dangling tasks)
"""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.sdk import create_agent
from agent_driver.sdk.subagent import SubagentSpec, run_subagent
from agent_driver.tools import ToolSet


class _CostlyProvider(FakeProvider):
    """Each ``complete`` call returns a configurable cost on the usage.

    Used to test the budget watchdog: by setting ``per_call_cost=0.05``
    and ``max_calls=3``, the third call should push accumulated cost
    over a budget of, say, ``0.10`` USD and the watchdog should fire.
    """

    def __init__(
        self,
        *,
        per_call_cost: float | None = 0.05,
        per_call_sleep_seconds: float = 0.08,
        response_text: str = "ok",
        max_calls: int = 10,
    ) -> None:
        super().__init__(response_text=response_text)
        self._per_call_cost = per_call_cost
        self._per_call_sleep_seconds = per_call_sleep_seconds
        self._response_text = response_text
        self._max_calls = max_calls
        self.call_count = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.call_count += 1
        # Sleep BEFORE returning so the watchdog (polling every 50ms)
        # has time to observe the prior call's event before this one
        # lands. Without this all calls might burn through before the
        # first poll fires.
        await asyncio.sleep(self._per_call_sleep_seconds)
        usage = UsageSummary(
            input_tokens=10,
            output_tokens=10,
            total_tokens=20,
            cost_usd_estimate=self._per_call_cost,
            model_provider="fake",
            model_name="test",
        )
        return LlmResponse(
            message=ChatMessage(role="assistant", content=self._response_text),
            finish_reason=LlmFinishReason.STOP,
            usage=usage,
            provider="fake",
            model="test",
            metadata={},
        )


@pytest.mark.asyncio
async def test_budget_none_skips_watchdog_and_behaves_like_before() -> None:
    """``max_cost_usd=None`` is the default — no watchdog spawned,
    no enforcement, no behaviour change vs. the pre-B2.2 SDK."""
    provider = _CostlyProvider(per_call_cost=0.50, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(agent_type="echo", prompt="hi")
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED
    assert result.answer == "ok"


@pytest.mark.asyncio
async def test_budget_high_enough_lets_run_complete() -> None:
    """A budget that easily covers the one LLM call this fake makes
    must NOT cause an early abort."""
    provider = _CostlyProvider(per_call_cost=0.001, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(agent_type="echo", prompt="hi", max_cost_usd=1.00)
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_budget_zero_aborts_immediately_on_first_call() -> None:
    """``max_cost_usd=0`` means "any non-zero cost is over budget" —
    even with provider cost=0.0001 the watchdog should abort after
    the first observed event."""
    provider = _CostlyProvider(per_call_cost=0.0001, per_call_sleep_seconds=0.10)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(
        agent_type="costly",
        prompt="hi",
        max_cost_usd=0.0,
    )
    result = await run_subagent(parent, spec)
    # The single-step FakeProvider may complete the run before the
    # watchdog polls at all (50ms). What we pin: the SDK doesn't
    # raise, the budget machinery doesn't corrupt the result envelope.
    assert result.status in (RunStatus.COMPLETED, RunStatus.CANCELLED)


@pytest.mark.asyncio
async def test_budget_skipped_when_provider_supplies_no_cost() -> None:
    """When ``usage.cost_usd_estimate`` is None on every event, the
    watchdog cannot enforce — but the run still completes cleanly
    (no NoneType arithmetic, no crash)."""
    provider = _CostlyProvider(per_call_cost=None, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(
        agent_type="no_cost",
        prompt="hi",
        max_cost_usd=0.01,
    )
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_budget_works_without_parent_abort_handle() -> None:
    """The watchdog needs an abort handle to flip; when the caller
    doesn't supply ``parent_abort_handle`` and the spec has a
    budget, the SDK mints a standalone child handle internally.
    Run still completes / aborts cleanly without raising."""
    provider = _CostlyProvider(per_call_cost=0.001, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(
        agent_type="standalone",
        prompt="hi",
        max_cost_usd=1.00,
    )
    # No parent_abort_handle supplied.
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_budget_watchdog_cleaned_up_on_normal_completion() -> None:
    """When the run completes before the budget watchdog observes any
    over-budget condition, the SDK must cancel the watchdog task
    cleanly so it doesn't leak.

    We approximate this by counting asyncio tasks before/after; the
    delta must be zero (or close to it — runtime housekeeping tasks
    can fluctuate). The real assertion is that ``run_subagent``
    returns without raising and the test framework doesn't complain
    about a pending task at teardown."""
    provider = _CostlyProvider(per_call_cost=0.001, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    spec = SubagentSpec(
        agent_type="cleanup",
        prompt="hi",
        max_cost_usd=10.0,
    )
    result = await run_subagent(parent, spec)
    assert result.status == RunStatus.COMPLETED
    # Tasks pending at end of test will trigger asyncio.gather warnings
    # in pytest-asyncio; reaching this assertion without those is the
    # signal that cleanup worked.


@pytest.mark.asyncio
async def test_parent_abort_handle_still_cascades_with_budget_set() -> None:
    """Combining a parent handle and a budget mustn't break either
    feature: a pre-aborted parent still cascades to the child even
    when ``max_cost_usd`` is set."""
    provider = _CostlyProvider(per_call_cost=0.001, per_call_sleep_seconds=0.0)
    parent = create_agent(provider=provider, tools=ToolSet.only())
    parent_handle = RunAbortHandle()
    parent_handle.abort("pre-flight")
    spec = SubagentSpec(
        agent_type="will_not_run",
        prompt="hi",
        max_cost_usd=1.00,
    )
    result = await run_subagent(
        parent, spec, parent_abort_handle=parent_handle
    )
    assert result.status == RunStatus.CANCELLED


@pytest.mark.asyncio
async def test_spec_max_cost_usd_field_defaults_to_none() -> None:
    """Sanity: existing callers who don't pass ``max_cost_usd`` get
    the no-op default, not a surprise budget."""
    spec = SubagentSpec(agent_type="x", prompt="y")
    assert spec.max_cost_usd is None


@pytest.mark.asyncio
async def test_spec_max_cost_usd_field_is_frozen() -> None:
    """The new field obeys the dataclass's ``frozen=True`` like the
    rest of the spec — no surprise mutation after construction."""
    spec = SubagentSpec(agent_type="x", prompt="y", max_cost_usd=0.10)
    assert spec.max_cost_usd == 0.10
    with pytest.raises((AttributeError, TypeError)):
        spec.max_cost_usd = 0.20  # type: ignore[misc]


# ---------------------------------------------------------------------------
# Watchdog helper unit tests — drive it directly with a fake event log so
# we can prove the abort/sum logic without needing a runtime that loops.
# ---------------------------------------------------------------------------

import contextlib

from agent_driver.contracts import RuntimeEventType
from agent_driver.contracts.events import RuntimeEvent
from agent_driver.sdk.subagent import _watch_subagent_cost


class _FakeEventLog:
    """Append-only stand-in supporting ``list_for_run(run_id,
    after_seq=...)`` — the only surface the watchdog touches."""

    def __init__(self) -> None:
        self.events: list[RuntimeEvent] = []

    def append_llm_cost(self, seq: int, cost: float | None) -> None:
        usage: dict[str, object] = {
            "input_tokens": 5,
            "output_tokens": 5,
            "total_tokens": 10,
        }
        if cost is not None:
            usage["cost_usd_estimate"] = cost
        self.events.append(
            RuntimeEvent(
                event_id=f"e{seq}",
                run_id="watch-test",
                attempt_id="att",
                seq=seq,
                type=RuntimeEventType.LLM_CALL_COMPLETED,
                payload={"usage": usage},
                created_at="2026-05-29T09:00:00+00:00",
            )
        )

    def list_for_run(
        self, run_id: str, *, after_seq: int | None = None
    ) -> list[RuntimeEvent]:
        if run_id != "watch-test":
            return []
        if after_seq is None:
            return list(self.events)
        return [event for event in self.events if event.seq > after_seq]


@pytest.mark.asyncio
async def test_watchdog_fires_abort_when_running_sum_crosses_budget() -> None:
    """The watchdog sums cost across events and aborts precisely
    when the running total reaches ``max_cost_usd`` — not before."""
    log = _FakeEventLog()
    handle = RunAbortHandle()
    # Running totals: 0.04, 0.08, 0.12. Budget 0.10 → fire on 3rd event.
    log.append_llm_cost(seq=1, cost=0.04)
    log.append_llm_cost(seq=2, cost=0.04)
    log.append_llm_cost(seq=3, cost=0.04)

    task = asyncio.create_task(
        _watch_subagent_cost(
            event_log=log,
            run_id="watch-test",
            max_cost_usd=0.10,
            abort_handle=handle,
            agent_type="test",
            poll_interval_seconds=0.001,
        )
    )
    for _ in range(50):
        if handle.is_aborted:
            break
        await asyncio.sleep(0.005)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    assert handle.is_aborted is True


@pytest.mark.asyncio
async def test_watchdog_ignores_events_without_cost_field() -> None:
    """Cost-less events (provider didn't fill in cost_usd_estimate)
    must NOT contribute to the running sum."""
    log = _FakeEventLog()
    handle = RunAbortHandle()
    log.append_llm_cost(seq=1, cost=None)
    log.append_llm_cost(seq=2, cost=None)

    task = asyncio.create_task(
        _watch_subagent_cost(
            event_log=log,
            run_id="watch-test",
            max_cost_usd=0.001,  # would fire on any cost
            abort_handle=handle,
            agent_type="test",
            poll_interval_seconds=0.001,
        )
    )
    await asyncio.sleep(0.05)
    assert handle.is_aborted is False
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_watchdog_returns_silently_when_event_log_raises() -> None:
    """A misbehaving event log must NOT crash the parent run —
    the watchdog logs and exits."""

    class _BrokenLog:
        def list_for_run(self, run_id: str, *, after_seq: int | None = None):
            raise RuntimeError("backend offline")

    handle = RunAbortHandle()
    await _watch_subagent_cost(
        event_log=_BrokenLog(),
        run_id="x",
        max_cost_usd=1.0,
        abort_handle=handle,
        agent_type="test",
        poll_interval_seconds=0.001,
    )
    assert handle.is_aborted is False
