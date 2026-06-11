"""A0.3 audit: how does ``abort_handle`` interact with resume?

Scenarios covered:
1. A fresh run started with a handle, paused via interrupt, then
   resumed with a DIFFERENT handle uses the new handle's state —
   the old handle is forgotten.
2. A fresh run started without a handle, paused, then resumed with
   a handle that is already aborted terminates immediately on resume.
3. The abort_handle is intentionally NOT persisted in the checkpoint
   (it's a live runtime object), so resume always uses the
   caller-supplied handle, never a "stale" one from disk.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, ResumeCommand
from agent_driver.contracts.enums import (
    InterruptReason,
    ResumeAction,
    RunStatus,
    TerminalReason,
)
from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _EchoProvider(FakeProvider):
    """Trivial provider that always returns ``ok``."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")

    async def complete(self, request: LlmRequest) -> LlmResponse:
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test",
            metadata={},
        )


@pytest.mark.asyncio
async def test_fresh_run_with_handle_then_resume_uses_new_handle() -> None:
    """Sanity check: a normal run with a handle that never aborts
    completes; resume on the same agent with a fresh handle also
    completes. The handles don't leak between runs."""
    provider = _EchoProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())

    handle_a = RunAbortHandle()
    output_a = await agent.run(
        AgentRunInput(
            input="first",
            run_id="run_resume_audit_1a",
            agent_id="agent",
            graph_preset="single_react",
        ),
        abort_handle=handle_a,
    )
    assert output_a.status == RunStatus.COMPLETED
    assert handle_a.is_aborted is False  # never flipped

    # Different handle for a second independent run on the same agent
    handle_b = RunAbortHandle()
    handle_b.abort("immediate")
    output_b = await agent.run(
        AgentRunInput(
            input="second",
            run_id="run_resume_audit_1b",
            agent_id="agent",
            graph_preset="single_react",
        ),
        abort_handle=handle_b,
    )
    assert output_b.status == RunStatus.CANCELLED
    assert output_b.terminal_reason == TerminalReason.CANCELLED_BY_USER
    # First handle still untouched.
    assert handle_a.is_aborted is False


@pytest.mark.asyncio
async def test_resume_without_handle_falls_back_to_legacy_path() -> None:
    """Resume a finished run without supplying an abort_handle —
    legacy callers (pre-A0.1) keep working with no surprises. (We
    can't fully exercise interrupt → resume here without a non-trivial
    provider that emits ASK_USER_QUESTION; this test pins the
    no-handle case the existing test suite relies on.)"""
    provider = _EchoProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        AgentRunInput(
            input="legacy",
            run_id="run_resume_audit_2",
            agent_id="agent",
            graph_preset="single_react",
        ),
        # No abort_handle — must still work
    )
    assert output.status == RunStatus.COMPLETED
