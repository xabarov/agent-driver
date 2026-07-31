"""Epic 037+: a throwing observer hook never changes the outcome nor crashes the run.

The invariant (reference: openclaude c23b6e1 — per-status consumer isolation): a lifecycle
observer that raises in after_llm_response / on_run_completed / on_error is logged and
skipped; it must not alter the tool/run outcome or propagate out of the dispatch loop.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, RunStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.lifecycle_hooks import (
    BaseRunLifecycleHook,
    dispatch_after_llm,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _ThrowingObserver(BaseRunLifecycleHook):
    name = "throwing_observer"

    def __init__(self) -> None:
        self.seen = 0

    async def after_llm_response(self, context, response) -> None:  # noqa: ANN001
        self.seen += 1
        raise RuntimeError("observer boom")

    async def on_run_completed(self, context, *, answer: str) -> None:  # noqa: ANN001
        raise RuntimeError("completed boom")


@pytest.mark.asyncio
async def test_dispatch_after_llm_isolates_a_throwing_observer() -> None:
    observer = _ThrowingObserver()
    # The dispatch loop must swallow the raise (not propagate) and still visit peers.
    peer_seen = {"n": 0}

    class _Peer(BaseRunLifecycleHook):
        name = "peer"

        async def after_llm_response(self, context, response) -> None:  # noqa: ANN001
            peer_seen["n"] += 1

    await dispatch_after_llm([observer, _Peer()], None, object())
    assert observer.seen == 1
    assert peer_seen["n"] == 1  # peer still fired despite the earlier raise


@pytest.mark.asyncio
async def test_throwing_observer_does_not_change_run_outcome() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="the answer"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_ThrowingObserver(),),
    )
    output = await agent.run(
        AgentRunInput(
            input="q",
            run_id="run_observer_boom",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    # The observer raised in after_llm_response AND on_run_completed; the run still
    # completes with the real answer.
    assert output.status == RunStatus.COMPLETED
    assert output.answer == "the answer"
