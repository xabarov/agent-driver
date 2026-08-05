"""EPIC-03 WP-B — lease lifecycle through the REAL runner.

Acquire/attach once, reuse across steps, and release/detach on every exit
(normal, fail-closed, exception). Driven through create_agent().run(...) so the
whole runner try/finally participates.
"""

import pytest

import agent_driver.execution as ex
from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.enums import RunStatus
from agent_driver.contracts.execution_lease import LeaseOwnership
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)


def _runner(backend, *, ownership=None, provider=None):
    return FakeSingleStepRunner(
        provider=provider or FakeProvider(response_text="done"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            execution_backend=backend, execution_lease_ownership=ownership
        ),
    )


def _input(run_id, app_metadata=None):
    return AgentRunInput(
        input="hi",
        run_id=run_id,
        agent_id="a",
        graph_preset="single_react",
        app_metadata=app_metadata or {},
    )


@pytest.mark.asyncio
async def test_runtime_owned_lease_acquired_once_and_released():
    backend = ex.FakeExecutionBackend()
    runner = _runner(backend, ownership=LeaseOwnership.RUNTIME_OWNED)
    out = await runner.run(_input("r1"))
    assert out.status == RunStatus.COMPLETED
    assert len(backend.lease_acquires) == 1
    assert len(backend.lease_releases) == 1  # released on normal exit
    assert backend.lease_detaches == []


@pytest.mark.asyncio
async def test_reuse_across_multiple_steps_acquires_once():
    # turn 1 emits a bash call (routed through the backend), turn 2 answers.
    class _BashThenAnswer(FakeProvider):
        def __init__(self):
            super().__init__(response_text="done")
            self._n = 0

        async def complete(self, request):
            self._n += 1
            if self._n == 1:
                return LlmResponse(
                    message=ChatMessage(role="assistant", content=""),
                    finish_reason=LlmFinishReason.TOOL_CALLS,
                    provider="p",
                    model="m",
                    metadata={
                        "planned_tool_calls": [
                            ToolCall(
                                tool_name="bash",
                                args={"command": "echo hi"},
                                tool_call_id="c1",
                            ).model_dump(mode="json")
                        ]
                    },
                )
            return await super().complete(request)

    backend = ex.FakeExecutionBackend(
        commands={"echo hi": ex.CommandOutcome(stdout="ok")}
    )
    # create_agent wires a REAL governed tool executor (FakeSingleStepRunner uses
    # a no-op executor, so tools would not actually run).
    agent = create_agent(
        provider=_BashThenAnswer(),
        tools=ToolSet.only("bash"),
        config=RunnerConfig(
            execution_backend=backend,
            execution_lease_ownership=LeaseOwnership.RUNTIME_OWNED,
        ),
    )
    out = await agent.run(_input("r2"))
    assert out.status == RunStatus.COMPLETED
    assert len(backend.command_calls) == 1  # the bash step ran through the lease
    assert len(backend.lease_acquires) == 1  # ONE acquire across both steps
    assert len(backend.lease_releases) == 1


@pytest.mark.asyncio
async def test_host_owned_lease_attaches_and_detaches_never_releases():
    backend = ex.FakeExecutionBackend()
    backend.known_generations["hostlease"] = "g1"
    ref = ex.ExecutionLeaseRef(
        lease_id="hostlease",
        generation="g1",
        backend_id="fake",
        ownership=LeaseOwnership.HOST_OWNED,
    )
    runner = _runner(backend)  # no config ownership -> attach path
    out = await runner.run(
        _input("r3", app_metadata={"execution_lease_ref": ref.model_dump(mode="json")})
    )
    assert out.status == RunStatus.COMPLETED
    assert backend.lease_acquires == []  # attached, not acquired
    assert len(backend.lease_detaches) == 1  # host-owned -> detach only
    assert backend.lease_releases == []  # never destroy host state


@pytest.mark.asyncio
async def test_lease_requested_but_backend_not_lease_capable_fails_closed():
    # Composite backend has no acquire_lease -> a requested lease fails closed;
    # the run must NOT fall back to local. Terminal FAILED, no acquire.
    backend = ex.CompositeExecutionBackend(file_io=None, command_runner=None)
    runner = _runner(backend, ownership=LeaseOwnership.RUNTIME_OWNED)
    out = await runner.run(_input("r4"))
    assert out.status == RunStatus.FAILED


@pytest.mark.asyncio
async def test_lease_released_even_when_run_raises():
    class _Boom(FakeProvider):
        async def complete(self, request):
            raise RuntimeError("provider exploded")

        async def stream(self, request):  # pragma: no cover - not used
            raise RuntimeError("provider exploded")
            yield

    backend = ex.FakeExecutionBackend()
    runner = _runner(backend, ownership=LeaseOwnership.RUNTIME_OWNED, provider=_Boom())
    with pytest.raises(Exception):
        await runner.run(_input("r5"))
    # the outer finally released the lease despite the exception
    assert len(backend.lease_releases) == 1
