"""Runtime integration tests for the ``abort_handle`` runtime kwarg.

These pin the contract: when the caller flips a
:class:`RunAbortHandle`, the next step boundary detects it and
terminates the run with ``RunStatus.CANCELLED`` /
``TerminalReason.CANCELLED_BY_USER``. Subagent inheritance is covered
separately in ``test_subagent_abort_inheritance.py`` (when subagents
land in B0.1).
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import RunStatus, TerminalReason
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.contracts.usage import UsageSummary
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _SlowProvider(FakeProvider):
    """Provider that finishes immediately but records each request.

    By itself it can't simulate an abort mid-call (the FakeProvider
    returns synchronously). Combined with a caller that aborts BEFORE
    the run starts or BETWEEN runs, we get coverage of the step-
    boundary check without needing a real I/O delay.
    """

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test",
            metadata={},
        )


@pytest.mark.asyncio
async def test_run_with_pre_aborted_handle_terminates_immediately() -> None:
    """If the handle is already aborted when the run starts, the
    first step-boundary check fires and the run terminates without
    ever calling the LLM provider."""
    handle = RunAbortHandle()
    handle.abort("pre-flight")
    provider = _SlowProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="hello",
            run_id="run_pre_aborted",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=8,
        ),
        abort_handle=handle,
    )
    assert output.status == RunStatus.CANCELLED
    assert output.terminal_reason == TerminalReason.CANCELLED_BY_USER
    # Provider should never have been called.
    assert provider.requests == []


@pytest.mark.asyncio
async def test_run_without_abort_handle_uses_legacy_behaviour() -> None:
    """Sanity check: when no handle is passed, the run completes
    normally — preserves backward compat for every existing caller."""
    provider = _SlowProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="hello",
            run_id="run_no_handle",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
        )
    )
    assert output.status == RunStatus.COMPLETED
    assert len(provider.requests) >= 1


@pytest.mark.asyncio
async def test_abort_after_run_completed_is_harmless() -> None:
    """Calling ``.abort()`` after a successful run completes does
    nothing meaningful — flag flips, but the output is already
    materialised."""
    handle = RunAbortHandle()
    provider = _SlowProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="hello",
            run_id="run_late_abort",
            agent_id="agent",
            graph_preset="single_react",
            max_steps=4,
        ),
        abort_handle=handle,
    )
    assert output.status == RunStatus.COMPLETED
    # Now flip the handle — should be a no-op for the already-produced output.
    handle.abort("too_late")
    assert handle.is_aborted is True
    # Output hasn't changed.
    assert output.status == RunStatus.COMPLETED


@pytest.mark.asyncio
async def test_cancellation_probe_and_abort_handle_both_work() -> None:
    """Both seams co-exist: a config-level ``cancellation_probe`` and
    a runtime ``abort_handle`` both terminate the run with the same
    terminal reason. Either fires the same outcome — this test pins
    the probe path."""
    from agent_driver.runtime.single_agent.types import RunnerConfig

    cancel_via_probe = {"flag": True}
    provider = _SlowProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        config=RunnerConfig(
            cancellation_probe=lambda: cancel_via_probe["flag"],
        ),
    )
    output = await agent.run(
        AgentRunInput(
            input="probe",
            run_id="run_probe_cancel",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    assert output.status == RunStatus.CANCELLED
    assert output.terminal_reason == TerminalReason.CANCELLED_BY_USER
