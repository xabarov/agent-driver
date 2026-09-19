"""AD-3: the run-scoped one-shot strategy grant for force planning.

The first force-planning strategy denial records the denied call's
fingerprint. The model's explicit ``continue_without_plan`` declaration
arms a one-shot retry grant bound to exactly that call; the evaluator
consumes it on use. A second gated call, a changed-args retry, a
re-declaration, and a new run all stay denied.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import SideEffectClass, ToolRisk
from agent_driver.contracts.tools import ToolCall, ToolManifest, ToolPolicyInput
from agent_driver.tools.context import (
    get_strategy_grant_ledger,
    reset_strategy_grant_ledger,
    strategy_call_fingerprint,
    strategy_grant_scope,
)
from agent_driver.tools.planning import _continue_without_plan_tool
from agent_driver.tools.policy import evaluate_tool_policy

import asyncio


def _manifest(name: str = "web_get") -> ToolManifest:
    return ToolManifest(
        name=name,
        description="GET one page",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.EXTERNAL_ACTION,
    )


def _policy(tool: str = "web_get") -> ToolPolicyInput:
    return ToolPolicyInput(
        metadata={
            "force_planning": {
                "mode": "strategy_required_before_execution",
                "gated_tools": [tool],
            }
        }
    )


@pytest.fixture(autouse=True)
def _fresh_ledger():
    with strategy_grant_scope():
        yield
    reset_strategy_grant_ledger()


def test_first_denial_binds_grant_to_that_exact_call() -> None:
    blocked = evaluate_tool_policy(
        policy=_policy(),
        manifest=_manifest(),
        call=ToolCall(tool_name="web_get", args={"url": "https://x"}),
        current_tool_calls=0,
    )
    assert blocked.decision.value == "deny"
    grant = blocked.metadata["force_planning"]["strategy_grant"]
    assert grant == {
        "armed": False,
        "consumed": False,
        "bound_to_this_call": True,
    }
    ledger = get_strategy_grant_ledger()
    assert ledger["first_denial_tool"] == "web_get"
    assert (
        ledger["first_denial_fingerprint"]
        == strategy_call_fingerprint("web_get", {"url": "https://x"})
    )


def test_declaration_allows_exactly_one_retry_of_the_denied_call() -> None:
    call = ToolCall(tool_name="web_get", args={"url": "https://x"})
    first = evaluate_tool_policy(
        policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
    )
    assert first.decision.value == "deny"

    declared = asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
    assert declared["strategy_grant"]["armed"] is True

    retry = evaluate_tool_policy(
        policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
    )
    assert retry.decision.value == "allow"

    follow_up = evaluate_tool_policy(
        policy=_policy(),
        manifest=_manifest(),
        call=ToolCall(tool_name="web_get", args={"url": "https://y"}),
        current_tool_calls=1,
    )
    assert follow_up.decision.value == "deny"
    assert (
        follow_up.metadata["force_planning"]["strategy_grant"]["consumed"] is True
    )


def test_changed_args_retry_is_denied_and_grant_stays_bound() -> None:
    call = ToolCall(tool_name="web_get", args={"url": "https://x"})
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
        ).decision.value
        == "deny"
    )
    asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
    changed = evaluate_tool_policy(
        policy=_policy(),
        manifest=_manifest(),
        call=ToolCall(tool_name="web_get", args={"url": "https://OTHER"}),
        current_tool_calls=0,
    )
    assert changed.decision.value == "deny"
    assert (
        changed.metadata["force_planning"]["strategy_grant"]["bound_to_this_call"]
        is False
    )
    # The grant is still armed for its originally denied call.
    retry = evaluate_tool_policy(
        policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
    )
    assert retry.decision.value == "allow"


def test_second_denial_never_rebinds_the_grant() -> None:
    first = ToolCall(tool_name="web_get", args={"url": "https://x"})
    second = ToolCall(tool_name="web_get", args={"url": "https://y"})
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=first, current_tool_calls=0
        ).decision.value
        == "deny"
    )
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=second, current_tool_calls=0
        ).decision.value
        == "deny"
    )
    asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
    # The grant retries the FIRST denied call, not the second.
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=first, current_tool_calls=0
        ).decision.value
        == "allow"
    )


def test_declaration_without_prior_denial_arms_nothing() -> None:
    declared = asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
    assert declared["strategy_grant"] == {
        "armed": False,
        "tool": None,
        "arm_refused": "no_denied_gated_call",
    }
    blocked = evaluate_tool_policy(
        policy=_policy(),
        manifest=_manifest(),
        call=ToolCall(tool_name="web_get", args={"url": "https://x"}),
        current_tool_calls=0,
    )
    assert blocked.decision.value == "deny"


def test_consumed_grant_cannot_be_rearmed_in_the_same_run() -> None:
    call = ToolCall(tool_name="web_get", args={"url": "https://x"})
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
        ).decision.value
        == "deny"
    )
    asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
    assert (
        evaluate_tool_policy(
            policy=_policy(), manifest=_manifest(), call=call, current_tool_calls=0
        ).decision.value
        == "allow"
    )
    refused = asyncio.run(_continue_without_plan_tool({"reason": "again"}))
    assert refused["strategy_grant"]["arm_refused"] == "grant_already_consumed"
    assert (
        evaluate_tool_policy(
            policy=_policy(),
            manifest=_manifest(),
            call=ToolCall(tool_name="web_get", args={"url": "https://z"}),
            current_tool_calls=1,
        ).decision.value
        == "deny"
    )


def test_new_run_starts_with_a_fresh_ledger() -> None:
    call = ToolCall(tool_name="web_get", args={"url": "https://x"})
    with strategy_grant_scope():
        assert (
            evaluate_tool_policy(
                policy=_policy(),
                manifest=_manifest(),
                call=call,
                current_tool_calls=0,
            ).decision.value
            == "deny"
        )
        asyncio.run(_continue_without_plan_tool({"reason": "one narrow GET"}))
        assert (
            evaluate_tool_policy(
                policy=_policy(),
                manifest=_manifest(),
                call=call,
                current_tool_calls=0,
            ).decision.value
            == "allow"
        )
    with strategy_grant_scope():
        # A new run: the previous grant is gone and the gate denies again.
        blocked = evaluate_tool_policy(
            policy=_policy(),
            manifest=_manifest(),
            call=call,
            current_tool_calls=0,
        )
        assert blocked.decision.value == "deny"
        assert (
            blocked.metadata["force_planning"]["strategy_grant"]["consumed"] is False
        )


def test_legacy_host_boolean_bypass_is_still_honored() -> None:
    policy = ToolPolicyInput(
        metadata={
            "force_planning": {
                "mode": "strategy_required_before_execution",
                "gated_tools": ["web_get"],
                "continue_without_plan": True,
            }
        }
    )
    allowed = evaluate_tool_policy(
        policy=policy,
        manifest=_manifest(),
        call=ToolCall(tool_name="web_get", args={"url": "https://x"}),
        current_tool_calls=5,
    )
    assert allowed.decision.value == "allow"
