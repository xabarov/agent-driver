"""opencode-adoption EPIC-03 — host tool-decision hooks.

A host registers ordered callbacks that see each planned call's resolved policy
decision and may only **tighten** it (allow < interrupt < deny). Contract pins:

* pure ``apply_decision_hooks`` — tighten wins, loosen ignored, feedback carried,
  a raising hook fails **closed** to DENY, empty hooks are a no-op;
* end-to-end through :class:`GovernedToolExecutor` — an allow->deny hook blocks a
  call the static policy allowed (handler never runs, envelope is ``policy_denied``
  and the steering feedback reaches the model via the error message);
* a hook that returns a *looser* verdict cannot un-block a policy DENY.
"""

from __future__ import annotations

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
from agent_driver.contracts.enums.tools import ToolPolicyDecision
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.policy.decision_hooks import (
    ToolDecisionHookResult,
    apply_decision_hooks,
    tighten_decision,
)
from tests.runtime.conftest import llm_request_with_planned_calls

ALLOW = ToolPolicyDecision.ALLOW
INTERRUPT = ToolPolicyDecision.INTERRUPT
DENY = ToolPolicyDecision.DENY


def _hook_args(decision: ToolPolicyDecision):
    """Minimal kwargs for a direct ``apply_decision_hooks`` call."""
    return dict(
        tool_name="t",
        args={},
        manifest=ToolManifest(name="t", description="d"),
        run_input=AgentRunInput(
            input="x", run_id="r", agent_id="a", graph_preset="single_react"
        ),
        decision=decision,
        reason="static",
    )


# -- pure apply_decision_hooks --------------------------------------------------


def test_tighten_decision_ladder():
    assert tighten_decision(ALLOW, DENY) is DENY
    assert tighten_decision(DENY, ALLOW) is DENY  # loosen ignored
    assert tighten_decision(ALLOW, INTERRUPT) is INTERRUPT
    assert tighten_decision(INTERRUPT, ALLOW) is INTERRUPT


def test_empty_hooks_is_noop():
    decision, reason, feedback = apply_decision_hooks((), **_hook_args(ALLOW))
    assert (decision, reason, feedback) == (ALLOW, "static", None)


def test_hook_tightens_allow_to_deny_with_feedback():
    def deny_hook(**_kw):
        return ToolDecisionHookResult(DENY, reason="blocked", feedback="use read_only")

    decision, reason, feedback = apply_decision_hooks(
        (deny_hook,), **_hook_args(ALLOW)
    )
    assert decision is DENY
    assert reason == "blocked"
    assert feedback == "use read_only"


def test_hook_cannot_loosen_a_deny():
    def loosen(**_kw):
        return ToolDecisionHookResult(ALLOW, reason="please allow")

    decision, reason, _ = apply_decision_hooks((loosen,), **_hook_args(DENY))
    assert decision is DENY
    assert reason == "static"  # untouched — the loosen proposal is ignored


def test_none_return_leaves_decision_unchanged():
    def abstain(**_kw):
        return None

    decision, reason, feedback = apply_decision_hooks(
        (abstain,), **_hook_args(ALLOW)
    )
    assert (decision, reason, feedback) == (ALLOW, "static", None)


def test_raising_hook_fails_closed_to_deny():
    def boom(**_kw):
        raise RuntimeError("kaboom")

    decision, reason, _ = apply_decision_hooks((boom,), **_hook_args(ALLOW))
    assert decision is DENY
    assert "decision hook error" in (reason or "")
    assert "RuntimeError" in (reason or "")


def test_hooks_compose_monotonically():
    # allow -> interrupt -> deny, each hook sees the tightened-so-far decision.
    seen: list[ToolPolicyDecision] = []

    def to_interrupt(*, decision, **_kw):
        seen.append(decision)
        return ToolDecisionHookResult(INTERRUPT)

    def to_deny(*, decision, **_kw):
        seen.append(decision)
        return ToolDecisionHookResult(DENY)

    decision, _, _ = apply_decision_hooks(
        (to_interrupt, to_deny), **_hook_args(ALLOW)
    )
    assert decision is DENY
    assert seen == [ALLOW, INTERRUPT]  # second hook saw the tightened decision


# -- end-to-end through GovernedToolExecutor -----------------------------------


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def _registry_with_flag() -> tuple[ToolRegistry, dict[str, bool]]:
    ran = {"called": False}

    async def _handler(_args):
        ran["called"] = True
        return {"ok": True}

    reg = ToolRegistry()
    reg.register(
        ToolManifest(
            name="safe_tool",
            description="benign read",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
        ),
        _handler,
    )
    return reg, ran


async def _execute_one(executor: GovernedToolExecutor, run_id: str):
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="safe_tool", tool_call_id="call_1", args={})
            ]
        )
    )
    return await executor.execute(_run_input(run_id), response)


@pytest.mark.asyncio
async def test_executor_hook_blocks_allowed_call():
    reg, ran = _registry_with_flag()

    def deny_hook(**_kw):
        return ToolDecisionHookResult(
            DENY, reason="host policy", feedback="prefer summarise_tool"
        )

    executor = GovernedToolExecutor(registry=reg, decision_hooks=(deny_hook,))
    result = await _execute_one(executor, "r_deny")

    assert ran["called"] is False  # handler never ran
    envelope = result.envelopes[0]
    assert envelope.decision is DENY
    assert envelope.error is not None
    assert envelope.error.code == "policy_denied"
    # steering feedback reaches the model via the folded reason/message
    assert "prefer summarise_tool" in (envelope.error.message or "")
    assert "host policy" in (envelope.error.message or "")


@pytest.mark.asyncio
async def test_executor_no_hooks_allows_call():
    reg, ran = _registry_with_flag()
    executor = GovernedToolExecutor(registry=reg)  # no decision hooks
    result = await _execute_one(executor, "r_allow")

    assert ran["called"] is True
    assert result.envelopes[0].decision is ALLOW


@pytest.mark.asyncio
async def test_executor_hook_abstain_allows_call():
    reg, ran = _registry_with_flag()

    def abstain(**_kw):
        return None

    executor = GovernedToolExecutor(registry=reg, decision_hooks=(abstain,))
    result = await _execute_one(executor, "r_abstain")

    assert ran["called"] is True
    assert result.envelopes[0].decision is ALLOW
