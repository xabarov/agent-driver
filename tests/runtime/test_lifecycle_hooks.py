"""Run lifecycle hook seam: custom hooks observe run start and finalize."""

from __future__ import annotations

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
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


@pytest.mark.asyncio
async def test_per_call_hooks_fire_around_provider_call() -> None:
    """before_llm_request and after_llm_response fire with the right objects."""

    class _CallHook(BaseRunLifecycleHook):
        name = "call"

        def __init__(self) -> None:
            self.before = 0
            self.after = 0
            self.saw_response = False

        async def before_llm_request(self, context, request):  # noqa: ANN001
            self.before += 1
            assert isinstance(request, LlmRequest)
            return None  # no transform

        async def after_llm_response(self, context, response):  # noqa: ANN001
            self.after += 1
            self.saw_response = isinstance(response, LlmResponse)

    hook = _CallHook()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        lifecycle_hooks=(hook,),
    )
    await agent.session("s1").send("hello", run_id="r1")

    assert hook.before >= 1
    assert hook.after >= 1
    assert hook.saw_response is True


class _InjectingProvider(FakeProvider):
    """Records whether the request carried a hook-injected metadata marker."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.saw_injected: str | None = None

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.saw_injected = request.metadata.get("injected")
        return await super().complete(request)


@pytest.mark.asyncio
async def test_before_llm_request_transforms_the_request() -> None:
    """A before_llm_request hook's returned request reaches the provider."""

    class _InjectHook(BaseRunLifecycleHook):
        name = "inject"

        async def before_llm_request(self, context, request):  # noqa: ANN001
            return request.model_copy(
                update={"metadata": {**request.metadata, "injected": "yes"}}
            )

    provider = _InjectingProvider()
    agent = create_agent(
        provider=provider, tools=ToolSet.only(), lifecycle_hooks=(_InjectHook(),)
    )
    await agent.session("s1").send("hello", run_id="r1")

    assert provider.saw_injected == "yes"


class _BoomHook(BaseRunLifecycleHook):
    """A hook that raises in every callback, to exercise dispatch isolation."""

    name = "boom"

    async def on_run_start(self, context) -> None:  # noqa: ANN001
        raise RuntimeError("boom: on_run_start")

    async def on_finalize(self, context, *, answer: str):  # noqa: ANN001
        raise RuntimeError("boom: on_finalize")

    async def before_llm_request(self, context, request):  # noqa: ANN001
        raise RuntimeError("boom: before_llm_request")

    async def after_llm_response(self, context, response) -> None:  # noqa: ANN001
        raise RuntimeError("boom: after_llm_response")


@pytest.mark.asyncio
async def test_failing_hook_is_isolated_and_others_still_fire() -> None:
    """A hook raising in every callback never aborts the run or blocks peers."""
    survivor = _RecordingHook()
    agent = create_agent(
        provider=FakeProvider(response_text="done"),
        tools=ToolSet.only(),
        lifecycle_hooks=(_BoomHook(), survivor),
    )
    output = await agent.session("sess-iso").send("hello", run_id="r-iso")

    assert output.status.value == "completed"  # the run survived the failing hook
    assert survivor.events == ["start:sess-iso", "finalize"]  # peer still fired
    assert survivor.answer == "done"
