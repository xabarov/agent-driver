"""Live subagent steering via a per-run redirect probe (coordination C3)."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.single_agent.llm_step.completion import (
    _run_redirect_probe,
    active_redirect_probe,
)
from agent_driver.sdk import SubagentSpec, ToolSet, create_agent, run_subagent
from agent_driver.sdk.async_subagent import AsyncSubagentManager


def test_active_redirect_probe_sets_and_resets_the_contextvar() -> None:
    def probe() -> str | None:
        return None

    assert _run_redirect_probe.get() is None
    with active_redirect_probe(probe):
        assert _run_redirect_probe.get() is probe
    assert _run_redirect_probe.get() is None


def test_active_redirect_probe_none_is_a_noop() -> None:
    with active_redirect_probe(None):
        assert _run_redirect_probe.get() is None


class _ProbeRecorder(FakeProvider):
    """Records the per-run redirect probe visible during each completion, by prompt."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.seen: dict[str, object] = {}

    async def complete(self, request):  # noqa: ANN001, ANN201
        user = next(
            (
                m.content
                for m in request.messages
                if getattr(m.role, "value", m.role) == "user"
            ),
            "",
        )
        self.seen[user] = _run_redirect_probe.get()
        return await super().complete(request)


@pytest.mark.asyncio
async def test_run_subagent_binds_the_probe_visible_to_completion() -> None:
    provider = _ProbeRecorder()
    parent = create_agent(provider=provider, tools=ToolSet.only())

    def probe() -> str | None:
        return None

    await run_subagent(
        parent, SubagentSpec(agent_type="w", prompt="task-A"), redirect_probe=probe
    )
    assert provider.seen["task-A"] is probe
    assert _run_redirect_probe.get() is None  # reset after the run


@pytest.mark.asyncio
async def test_concurrent_children_have_isolated_probes() -> None:
    provider = _ProbeRecorder()
    parent = create_agent(provider=provider, tools=ToolSet.only())

    def probe_a() -> str | None:
        return None

    def probe_b() -> str | None:
        return None

    await asyncio.gather(
        run_subagent(parent, SubagentSpec(agent_type="a", prompt="A"), redirect_probe=probe_a),
        run_subagent(parent, SubagentSpec(agent_type="b", prompt="B"), redirect_probe=probe_b),
    )
    # Each child's completion saw ITS OWN probe — not the other's, not a shared one.
    assert provider.seen["A"] is probe_a
    assert provider.seen["B"] is probe_b


@pytest.mark.asyncio
async def test_no_probe_leaves_completion_with_none() -> None:
    provider = _ProbeRecorder()
    parent = create_agent(provider=provider, tools=ToolSet.only())
    await run_subagent(parent, SubagentSpec(agent_type="w", prompt="P"))
    assert provider.seen["P"] is None  # no per-run probe → falls back to config (None here)


@pytest.mark.asyncio
async def test_background_subagent_send_feeds_the_steering_probe() -> None:
    parent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())
    manager = AsyncSubagentManager(parent=parent)
    handle = manager.start(SubagentSpec(agent_type="bg", prompt="go"))

    handle.send("  change approach  ")
    handle.send("")  # empty ignored
    handle.send("also this")
    assert handle._steer == ["change approach", "also this"]

    # The steer queue IS the probe's source: popping yields the messages in order.
    assert handle._steer.pop(0) == "change approach"
    await handle.result()
