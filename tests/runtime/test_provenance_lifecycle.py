"""R1 (epic 057) — ToolGate provenance full lifecycle: terminal + trace projection.

Extends the U2 provenance slice (``test_tool_gate_provenance.py``, which locked
the outcome fold + ask→interrupt + fail-closed) to the parts the 0.2.0 handoff
left open:

- provenance survives onto the executed **envelope** for allow / deny / ask
  (not just the ask interrupt);
- it reaches the **terminal / trace projection** (the ``runtime_decision`` event
  the host reads) under the redaction-safe ``redacted_metadata`` field;
- a **gate** decision is told apart from a **static** policy decision there
  (``policy_id="tool_gate"`` vs ``"tool_policy"``) via the reserved gate-decision
  marker, without pattern-matching reason strings;
- **call identity** (``tool_call_id``) is stable from the gate context through to
  the terminal projection;
- host provenance stays **unforgeable** and **fails closed** end-to-end.
"""

from __future__ import annotations

from typing import Any

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.enums import RuntimeEventType, ToolPolicyDecision
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.runtime.tool_gate import (
    RESERVED_GATE_DECISION_KEY,
    RESERVED_GATE_PROVENANCE_KEY,
    GateProvenance,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls

# --------------------------------------------------------------------------- #
# Executor-level: provenance + gate-decision marker land on the envelope
# --------------------------------------------------------------------------- #


def _manifest(name: str = "danger") -> ToolManifest:
    return ToolManifest(
        name=name,
        description="side-effecting tool",
        risk=ToolRisk.HIGH,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        approval_mode=ApprovalMode.NEVER,
    )


def _executor() -> GovernedToolExecutor:
    registry = ToolRegistry()

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True}

    registry.register(_manifest(), _handler)
    return GovernedToolExecutor(registry=registry)


def _run_input() -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id="run_prov",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


async def _execute(gate) -> Any:
    executor = _executor()
    provider = FakeProvider(response_text="ok")
    call = ToolCall(tool_name="danger", tool_call_id="tc-7", args={"target": "x"})
    response = await provider.complete(llm_request_with_planned_calls(planned=[call]))
    return await executor.execute(_run_input(), response, tool_gate=gate)


@pytest.mark.asyncio
async def test_allow_provenance_on_envelope() -> None:
    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        return ToolGateAllow(provenance=GateProvenance(decision_id="d-allow"))

    result = await _execute(gate)
    meta = result.envelopes[0].metadata
    assert meta[RESERVED_GATE_DECISION_KEY] == "allow"
    assert meta[RESERVED_GATE_PROVENANCE_KEY]["decision_id"] == "d-allow"
    # Identity is stable from the gate context to the executed envelope.
    assert result.envelopes[0].call.tool_call_id == "tc-7"


@pytest.mark.asyncio
async def test_deny_provenance_on_envelope() -> None:
    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(
            reason="nope", provenance=GateProvenance(policy_snapshot_id="snap-1")
        )

    result = await _execute(gate)
    env = result.envelopes[0]
    assert env.decision == ToolPolicyDecision.DENY
    assert env.metadata[RESERVED_GATE_DECISION_KEY] == "deny"
    assert env.metadata[RESERVED_GATE_PROVENANCE_KEY]["policy_snapshot_id"] == "snap-1"


@pytest.mark.asyncio
async def test_static_allow_has_no_gate_marker() -> None:
    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        return ToolGateAllow()  # transparent allow, no provenance

    result = await _execute(gate)
    assert RESERVED_GATE_DECISION_KEY not in (result.envelopes[0].metadata or {})


# --------------------------------------------------------------------------- #
# Runner-level: provenance reaches the terminal RuntimeDecision projection
# --------------------------------------------------------------------------- #


def _runner():
    registry = ToolRegistry()

    async def _danger(args):
        return {"summary": "ok"}

    registry.register(_manifest(), _danger)
    event_log = InMemoryEventLog()
    runner = FakeSingleStepRunner(
        provider=FakeProvider(response_text="ok"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            ),
        ),
    )
    return runner, event_log


def _run_input_planned(run_id: str, *, mode=ToolPolicyMode.ALLOW_TOOLS) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=mode,
            metadata={
                "planned_tool_calls": [
                    {
                        "tool_name": "danger",
                        "tool_call_id": "tc-gate",
                        "args": {"target": "x"},
                    }
                ]
            },
        ),
    )


def _runtime_decisions(event_log, run_id: str) -> list[dict[str, Any]]:
    return [
        event.payload
        for event in event_log.list_for_run(run_id)
        if event.type == RuntimeEventType.RUNTIME_DECISION
    ]


@pytest.mark.asyncio
async def test_gate_deny_reaches_terminal_projection_with_provenance() -> None:
    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(
            reason="blocked",
            provenance=GateProvenance(
                decision_id="dec-1", policy_snapshot_id="snap-1", metadata={"z": "q"}
            ),
        )

    runner, event_log = _runner()
    await runner.run(_run_input_planned("run_gate_deny"), tool_gate=gate)
    decisions = _runtime_decisions(event_log, "run_gate_deny")
    gate_denies = [d for d in decisions if d["policy_id"] == "tool_gate"]
    assert len(gate_denies) == 1, decisions
    d = gate_denies[0]
    assert d["trigger"] == "tool_gate_denied"
    assert d["action"] == "block"
    # Trace-safe projection carries the provenance + marker + call identity.
    rm = d["redacted_metadata"]
    assert rm[RESERVED_GATE_DECISION_KEY] == "deny"
    assert rm[RESERVED_GATE_PROVENANCE_KEY]["decision_id"] == "dec-1"
    # Call identity is stable from the gate context to the terminal projection.
    assert rm["tool_call_id"] == "tc-gate"


@pytest.mark.asyncio
async def test_static_deny_is_distinct_from_gate_deny() -> None:
    # NO_TOOLS produces a *static* policy denial — no gate marker, tagged
    # ``tool_policy``, so a host can tell it apart from a gate denial.
    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        return ToolGateAllow()

    runner, event_log = _runner()
    await runner.run(_run_input_planned("run_static_deny", mode=ToolPolicyMode.NO_TOOLS), tool_gate=gate)
    decisions = _runtime_decisions(event_log, "run_static_deny")
    assert decisions, "expected a runtime_decision for the static denial"
    assert all(d["policy_id"] != "tool_gate" for d in decisions), decisions
    assert not any(
        (d.get("redacted_metadata") or {}).get(RESERVED_GATE_DECISION_KEY)
        for d in decisions
    )


@pytest.mark.asyncio
async def test_gate_ask_terminal_projection_and_interrupt_identity() -> None:
    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(
            message="Approve?",
            provenance=GateProvenance(decision_id="dec-ask"),
        )

    runner, _event_log = _runner()
    paused = await runner.run(_run_input_planned("run_gate_ask"), tool_gate=gate)
    assert paused.status.value == "paused"
    # For an ask the run pauses BEFORE tool completion, so the interrupt the host
    # resumes against IS the terminal projection surface — it carries the gate
    # provenance + marker + stable call identity.
    interrupt_meta = paused.interrupt.metadata or {}
    assert interrupt_meta[RESERVED_GATE_PROVENANCE_KEY]["decision_id"] == "dec-ask"
    assert interrupt_meta[RESERVED_GATE_DECISION_KEY] == "ask"


@pytest.mark.asyncio
async def test_forged_reserved_provenance_fails_closed_end_to_end() -> None:
    # A host that (mis)uses the reserved namespace inside its own metadata must
    # be rejected — the gate call fails closed (DENY), unforgeable.
    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        return ToolGateAllow(
            provenance=GateProvenance(metadata={"_ad_forged": "x"})
        )

    runner, event_log = _runner()
    await runner.run(_run_input_planned("run_forged"), tool_gate=gate)
    decisions = _runtime_decisions(event_log, "run_forged")
    # Fails closed to a denial; the forged reserved key never becomes provenance.
    assert any(d["action"] == "block" for d in decisions), decisions
    for d in decisions:
        assert "_ad_forged" not in (d.get("redacted_metadata") or {})
