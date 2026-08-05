"""EPIC-01 acceptance scenarios through the REAL runner + governed tool path.

These drive `create_agent(...).run(...)` with a scripted provider that emits a
real `bash` tool call, so the run goes LLM -> governance -> tool handler ->
injected backend, exactly as production does. Direct protocol mocks are covered
in test_backends/test_runtime_wiring; this file proves the end-to-end pipeline.
"""

import asyncio

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.contracts.enums import RunStatus
from agent_driver.execution import CommandOutcome, FakeExecutionBackend
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
)
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def _bash_run_input(
    run_id: str, command: str, *, tool_call_id: str = "tc-1"
) -> AgentRunInput:
    # The plain FakeProvider echoes request.metadata back into the response, so
    # this seeds exactly one planned bash call through the real executor.
    return AgentRunInput(
        input="run a command",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy={
            "metadata": {
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="bash",
                        args={"command": command},
                        tool_call_id=tool_call_id,
                    ).model_dump(mode="json")
                ]
            }
        },
    )


def _agent():
    return create_agent(
        provider=FakeProvider(response_text="done"), tools=ToolSet.only("bash")
    )


# Scenario 1 — no configured backend: bash behaves as before (local subprocess).
@pytest.mark.asyncio
async def test_no_backend_runs_bash_locally():
    output = await _agent().run(_bash_run_input("s1", "echo hi"))
    bash = [t for t in output.tool_trace if t.tool_name == "bash"]
    assert bash, "expected a bash trace"
    assert bash[0].status.value in {"ok", "success", "completed"}


# Scenarios 2 + 3 — a configured backend routes bash and receives the correct
# run/attempt/tool-call/request identity, and ONLY because governance allowed it.
@pytest.mark.asyncio
async def test_backend_routes_bash_with_post_gate_identity():
    backend = FakeExecutionBackend(
        commands={"echo hi": CommandOutcome(stdout="ROUTED", exit_code=0)}
    )
    output = await _agent().run(
        _bash_run_input("s2", "echo hi", tool_call_id="tc-s2"),
        execution_backend=backend,
    )
    # routed through the backend...
    assert [c.command for c in backend.command_calls] == ["echo hi"]
    # ...with the executor-enriched identity captured only after the gate
    # allowed the call: run/tool-call ids flow end-to-end, request_id defaults
    # to the tool_call_id for per-call idempotency.
    ident = backend.command_calls[0].identity
    assert ident.run_id == "s2"
    assert ident.tool_call_id == "tc-s2"
    assert ident.request_id == "tc-s2"
    assert ident.attempt_id
    assert output.status == RunStatus.COMPLETED


# Scenario 4 — a denied tool call never reaches the backend.
@pytest.mark.asyncio
async def test_denied_call_never_reaches_backend():
    backend = FakeExecutionBackend()
    gate = build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD))
    output = await _agent().run(
        _bash_run_input("s4", "rm -rf /"),
        tool_gate=gate,
        execution_backend=backend,
    )
    bash = [t for t in output.tool_trace if t.tool_name == "bash"]
    assert bash and bash[0].status.value == "denied"
    # governance is above dispatch: the backend was never invoked.
    assert backend.command_calls == []


# Scenario 6 — a backend failure becomes a bounded tool failure and the run
# still reaches a valid terminal (no unhandled crash out of run()).
@pytest.mark.asyncio
async def test_backend_failure_becomes_bounded_tool_failure():
    backend = FakeExecutionBackend(raise_timeout_for={"echo hi"})
    output = await _agent().run(
        _bash_run_input("s6", "echo hi"), execution_backend=backend
    )
    assert output.status in {RunStatus.COMPLETED, RunStatus.FAILED}
    bash = [t for t in output.tool_trace if t.tool_name == "bash"]
    assert bash, "expected a bash trace row even on backend failure"
    # the backend WAS reached (post-gate) and raised; the run survived it.
    assert [c.command for c in backend.command_calls] == ["echo hi"]


# Scenario 7 — concurrent runs do not leak backend context into each other.
@pytest.mark.asyncio
async def test_concurrent_runs_do_not_leak_backend():
    a = FakeExecutionBackend(commands={"echo A": CommandOutcome(stdout="A")})
    b = FakeExecutionBackend(commands={"echo B": CommandOutcome(stdout="B")})
    out_a, out_b = await asyncio.gather(
        _agent().run(_bash_run_input("cA", "echo A"), execution_backend=a),
        _agent().run(_bash_run_input("cB", "echo B"), execution_backend=b),
    )
    assert out_a.status == RunStatus.COMPLETED
    assert out_b.status == RunStatus.COMPLETED
    assert [c.command for c in a.command_calls] == ["echo A"]
    assert [c.command for c in b.command_calls] == ["echo B"]
    assert a.command_calls[0].identity.run_id == "cA"
    assert b.command_calls[0].identity.run_id == "cB"
