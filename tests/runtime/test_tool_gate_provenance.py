"""U2 (epic 050) — tool-gate call identity + decision provenance.

Coverage:
- ToolGateContext now exposes stable call identity (tool_call_id, attempt_id).
- A gate result carrying GateProvenance folds validated provenance into the
  tool policy outcome under the reserved ``_ad_`` namespace.
- The ask path forwards that provenance into the approval interrupt the host
  resumes against (interrupt + envelope metadata).
- Malformed / oversized / reserved-key / non-JSON provenance fails closed (DENY)
  rather than passing unaudited.
- Model/tool output cannot forge the reserved namespace: host provenance
  metadata using an ``_ad_`` key is rejected.

These target the governed executor directly so the contract is locked at the
right layer (mirrors ``tests/runtime/test_tool_gate.py``).
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
from agent_driver.contracts.enums import ToolPolicyDecision
from agent_driver.contracts.tools import ToolPolicyOutcome
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import (
    RESERVED_GATE_PROVENANCE_KEY,
    GateProvenance,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls


def _read_manifest(name: str = "lookup") -> ToolManifest:
    return ToolManifest(
        name=name,
        description="Read-only lookup tool",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
    )


def _executor_with_lookup() -> GovernedToolExecutor:
    registry = ToolRegistry()

    async def _handler(args: dict[str, Any]) -> dict[str, Any]:
        return {"ok": True, "echo": args}

    registry.register(_read_manifest(), _handler)
    return GovernedToolExecutor(registry=registry)


def _run_input() -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id="run_gate_test",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


async def _execute(executor, run_input, planned, *, tool_gate=None):
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(planned=[planned])
    )
    return await executor.execute(run_input, response, tool_gate=tool_gate)


# --------------------------------------------------------------------------- #
# Phase A — call identity in the gate context
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_gate_context_carries_call_identity() -> None:
    executor = _executor_with_lookup()
    seen: list[ToolGateContext] = []

    async def gate(ctx: ToolGateContext) -> ToolGateAllow:
        seen.append(ctx)
        return ToolGateAllow()

    call = ToolCall(tool_name="lookup", tool_call_id="tc-42", args={"q": "x"})
    await _execute(executor, _run_input(), call, tool_gate=gate)
    assert seen and seen[0].tool_call_id == "tc-42"
    assert seen[0].attempt_id == "attempt_1"


# --------------------------------------------------------------------------- #
# Phase B — provenance channel folded onto the outcome (allow/deny)
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result",
    [
        ToolGateAllow(
            provenance=GateProvenance(decision_id="d1", metadata={"eng": "opaque"})
        ),
        ToolGateDeny(
            reason="blocked",
            provenance=GateProvenance(policy_snapshot_id="snap7"),
        ),
    ],
)
async def test_provenance_folded_onto_outcome(result) -> None:
    executor = _executor_with_lookup()
    policy = ToolPolicyOutcome(decision=ToolPolicyDecision.ALLOW, reason="ok")

    async def gate(ctx: ToolGateContext):
        return result

    outcome = await executor._apply_tool_gate(
        gate=gate,
        policy=policy,
        call=ToolCall(tool_name="lookup", tool_call_id="tc1", args={}),
        manifest=_read_manifest(),
        run_input=_run_input(),
        current_tool_calls=0,
        attempt_id="attempt_1",
    )
    packed = outcome.metadata.get(RESERVED_GATE_PROVENANCE_KEY)
    assert packed is not None
    assert packed["decision_id"] == result.provenance.decision_id
    assert packed["policy_snapshot_id"] == result.provenance.policy_snapshot_id


# --------------------------------------------------------------------------- #
# Phase C (slice) — ask path forwards provenance into the interrupt
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
async def test_ask_provenance_reaches_interrupt() -> None:
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(
            message="Approve?",
            provenance=GateProvenance(
                decision_id="dec-9", policy_snapshot_id="snap-3"
            ),
        )

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert result.interrupt is not None
    packed = result.interrupt.metadata.get(RESERVED_GATE_PROVENANCE_KEY)
    assert packed is not None and packed["decision_id"] == "dec-9"
    # It also rides the interrupt envelope metadata.
    env = result.envelopes[0]
    assert RESERVED_GATE_PROVENANCE_KEY in env.metadata


# --------------------------------------------------------------------------- #
# Phase D — fail-closed on malformed / reserved-key / oversized provenance
# --------------------------------------------------------------------------- #


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_metadata",
    [
        {"_ad_forged": "model-authored"},  # reserved namespace collision
        {"blob": "x" * 20_000},  # oversized
        {"deep": {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": 1}}}}}}}}},  # too deep
    ],
)
async def test_invalid_provenance_fails_closed(bad_metadata) -> None:
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateAsk:
        return ToolGateAsk(
            message="Approve?", provenance=GateProvenance(metadata=bad_metadata)
        )

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    # Fails closed: no interrupt, the call is denied (blocked envelope), and the
    # forged/oversized provenance never lands on any outcome.
    assert result.interrupt is None
    assert result.envelopes[0].decision == ToolPolicyDecision.DENY
    for env in result.envelopes:
        assert RESERVED_GATE_PROVENANCE_KEY not in (env.metadata or {})


@pytest.mark.asyncio
async def test_non_json_provenance_fails_closed() -> None:
    executor = _executor_with_lookup()

    async def gate(ctx: ToolGateContext) -> ToolGateDeny:
        return ToolGateDeny(
            reason="x", provenance=GateProvenance(metadata={"bad": object()})
        )

    call = ToolCall(tool_name="lookup", tool_call_id="tc1", args={"q": "x"})
    result = await _execute(executor, _run_input(), call, tool_gate=gate)
    assert result.envelopes[0].decision == ToolPolicyDecision.DENY
