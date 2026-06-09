"""Run lifecycle hook seam: custom hooks observe run start and finalize."""

from __future__ import annotations

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.lifecycle_hooks import BaseRunLifecycleHook
from agent_driver.sdk import ToolSet, create_agent


class _RecordingHook(BaseRunLifecycleHook):
    name = "recording"

    def __init__(self) -> None:
        self.events: list[str] = []
        self.answer: str | None = None

    async def on_run_start(self, context) -> None:  # noqa: ANN001
        self.events.append(f"start:{context.run_input.thread_id}")

    async def on_finalize(self, context, *, answer: str) -> None:  # noqa: ANN001
        self.events.append("finalize")
        self.answer = answer


@pytest.mark.asyncio
async def test_lifecycle_hook_fires_on_run_start_and_finalize() -> None:
    """A registered hook sees run start then finalize, with the final answer."""
    hook = _RecordingHook()
    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(hook,),
    )
    await agent.session("sess-1").send("hello", run_id="r1")

    assert hook.events == ["start:sess-1", "finalize"]
    assert hook.answer == "done"


@pytest.mark.asyncio
async def test_multiple_hooks_fire_in_registration_order() -> None:
    """Hooks are dispatched in the order they were registered."""
    order: list[str] = []

    class _A(BaseRunLifecycleHook):
        name = "a"

        async def on_run_start(self, context) -> None:  # noqa: ANN001
            order.append("a")

    class _B(BaseRunLifecycleHook):
        name = "b"

        async def on_run_start(self, context) -> None:  # noqa: ANN001
            order.append("b")

    agent = create_agent(
        provider=FakeProvider(response_text="x"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_A(), _B()),
    )
    await agent.session("s").send("hi", run_id="r1")

    assert order == ["a", "b"]
