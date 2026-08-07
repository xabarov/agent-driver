"""Epic 030: steering v2 — dispatcher completion + hard redirect."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.control import ControlKind, ControlPriority, ControlRequest
from agent_driver.contracts.enums import RunStatus
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.control.in_memory import InMemoryCommandQueueStore
from agent_driver.runtime.single_agent.llm_step.completion import (
    RedirectRequested,
    _await_with_redirect,
)
from agent_driver.runtime.single_agent.types import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="Какие решения по офису?",
        run_id=run_id,
        thread_id="t",
        agent_id="agent",
        graph_preset="single_react",
    )


# --- Phase A: unsupported kind emits a signal, is not silently dropped ---------


@pytest.mark.asyncio
async def test_unsupported_control_kind_emits_signal_and_marks_failed() -> None:
    store = InMemoryCommandQueueStore()
    item = store.enqueue(
        ControlRequest(
            kind=ControlKind.STOP_SUBAGENT,
            run_id="r-unsupported",
            priority=ControlPriority.NEXT,
        )
    )
    out = await create_agent(
        provider=FakeProvider(response_text="готово"),
        tools=ToolSet.only(),
        command_queue_store=store,
    ).run(_run_input("r-unsupported"))

    signals = [
        e.payload.get("signal_id") for e in out.events if e.payload.get("signal_id")
    ]
    assert "control_kind_unsupported" in signals
    # The item is marked FAILED (not left QUEUED to re-drain every step).
    assert store.get(item.queue_id).status.value == "failed"


# --- Phase B: hard redirect aborts the in-flight call + re-asks ----------------


class _SlowThenFastProvider(FakeProvider):
    """First completion sleeps (so a redirect can abort it); the rest are fast."""

    def __init__(self) -> None:
        super().__init__(response_text="готово")
        self.calls = 0
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls += 1
        self.requests.append(request)
        if self.calls == 1:
            await asyncio.sleep(3.0)  # aborted by the redirect probe
        return await super().complete(request)


@pytest.mark.asyncio
async def test_redirect_probe_aborts_and_reasks_with_correction() -> None:
    provider = _SlowThenFastProvider()
    fired = {"done": False}

    def probe() -> str | None:
        if not fired["done"]:
            fired["done"] = True
            return "поправка: только по покупке офиса"
        return None

    out = await asyncio.wait_for(
        create_agent(
            provider=provider,
            tools=ToolSet.only(),
            config=RunnerConfig(redirect_probe=probe),
        ).run(_run_input("r-redirect")),
        timeout=10,
    )

    assert out.status == RunStatus.COMPLETED
    # First call aborted, second (re-ask) completed.
    assert provider.calls == 2
    # The correction landed as a real user turn in the re-asked request.
    second = provider.requests[1]
    assert any("только по покупке офиса" in (m.content or "") for m in second.messages)
    # A raw-free redirect signal was emitted.
    assert any(
        e.payload.get("signal_id") == "steering_redirect_applied" for e in out.events
    )


@pytest.mark.asyncio
async def test_redirect_inert_without_probe() -> None:
    """No probe → the loop is unchanged (no redirect signal emitted)."""
    fast = FakeProvider(response_text="готово")
    out = await create_agent(provider=fast, tools=ToolSet.only()).run(
        _run_input("r-noprobe")
    )
    assert out.status == RunStatus.COMPLETED
    assert not any(
        e.payload.get("signal_id") == "steering_redirect_applied" for e in out.events
    )


@pytest.mark.asyncio
async def test_await_with_redirect_raises_on_probe() -> None:
    class _Host:
        class _Cfg:
            redirect_probe = staticmethod(lambda: "стоп, поправка")

        _config = _Cfg()

    async def _slow() -> str:
        await asyncio.sleep(3.0)
        return "never"

    with pytest.raises(RedirectRequested) as exc:
        await asyncio.wait_for(_await_with_redirect(_Host(), _slow()), timeout=5)
    assert exc.value.text == "стоп, поправка"


# --- Phase C: leftover protocol -----------------------------------------------


@pytest.mark.asyncio
async def test_leftover_controls_surfaced_in_terminal_metadata() -> None:
    store = InMemoryCommandQueueStore()
    # LATER priority is never drained at a step boundary → still QUEUED at
    # finalization → must surface as leftover for the host's next turn.
    store.enqueue(
        ControlRequest(
            kind=ControlKind.ENQUEUE_USER_MESSAGE,
            run_id="r-leftover",
            priority=ControlPriority.LATER,
            payload={"message": "а ещё добавь сроки"},
        )
    )
    out = await create_agent(
        provider=FakeProvider(response_text="готово"),
        tools=ToolSet.only(),
        command_queue_store=store,
    ).run(_run_input("r-leftover"))

    leftover = (out.metadata or {}).get("leftover_controls")
    assert leftover, "LATER steering message should surface as leftover"
    assert leftover[0]["kind"] == "enqueue_user_message"
    assert len(leftover[0]["content_sha256"]) == 64
    assert "text_preview" not in leftover[0]


# --- A3: steering pause / resume -----------------------------------------------


@pytest.mark.asyncio
async def test_pause_control_parks_run_as_resumable_paused() -> None:
    """A PAUSE control drained at the step boundary parks the run as PAUSED
    (MANUAL_PAUSE), and a ResumeCommand(CONTINUE) resumes it to completion."""
    from agent_driver.contracts.enums import InterruptReason, ResumeAction

    store = InMemoryCommandQueueStore()
    store.enqueue(
        ControlRequest(
            kind=ControlKind.PAUSE,
            run_id="r-pause",
            priority=ControlPriority.NOW,
        )
    )
    agent = create_agent(
        provider=FakeProvider(response_text="готово"),
        tools=ToolSet.only(),
        command_queue_store=store,
    )
    paused = await agent.run(_run_input("r-pause"))
    assert paused.status == RunStatus.PAUSED
    assert paused.interrupt is not None
    assert paused.interrupt.reason == InterruptReason.MANUAL_PAUSE
    assert ResumeAction.CONTINUE in paused.interrupt.allowed_actions

    resumed = await agent.resume(
        run_id="r-pause",
        interrupt_id=paused.interrupt.interrupt_id,
        action=ResumeAction.CONTINUE,
    )
    assert resumed.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_run_without_pause_is_unaffected() -> None:
    """No PAUSE control → the run completes normally (pause path is inert)."""
    out = await create_agent(
        provider=FakeProvider(response_text="готово"),
        tools=ToolSet.only(),
        command_queue_store=InMemoryCommandQueueStore(),
    ).run(_run_input("r-nopause"))
    assert out.status == RunStatus.COMPLETED
