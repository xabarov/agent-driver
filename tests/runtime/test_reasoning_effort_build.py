"""R1 — run_input.reasoning_effort resolves into LlmRequest.reasoning at build time,
and a live SET_MAX_THINKING_TOKENS budget still takes precedence."""

from __future__ import annotations

from collections.abc import Iterator
from types import SimpleNamespace

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.runtime.single_agent.llm_step.build import (
    LlmRequestBuildContext,
    build_single_agent_llm_request,
)


class _EmptyRegistry:
    def list_registered(self) -> Iterator[SimpleNamespace]:
        return iter(())


def _ctx(**run_kwargs) -> LlmRequestBuildContext:
    run_input = AgentRunInput(
        input="hi", agent_id="a", graph_preset="single_react", **run_kwargs
    )
    return LlmRequestBuildContext(
        run_input=run_input,
        clarification=None,
        tool_docs=None,
        authorized_imports=tuple(),
        registry=_EmptyRegistry(),
        observations=tuple(),
        planning_prompt=None,
        digest_ids=tuple(),
        artifact_ids=tuple(),
        max_chars=4000,
        max_messages=10,
        max_observations=None,
        context_window_estimate=12000,
        warning_threshold=7500,
        compact_threshold=9000,
        blocking_threshold=10500,
        output_token_reserve=1500,
        stream=False,
        system_instruction=None,
        protocol_messages=None,
        tool_choice=None,
        request_allowed_tools=None,
    )


def test_effort_becomes_reasoning_envelope():
    req, _ = build_single_agent_llm_request(_ctx(reasoning_effort="high"))
    assert req.reasoning == {"effort": "high"}


def test_no_effort_leaves_reasoning_none():
    req, _ = build_single_agent_llm_request(_ctx())
    assert req.reasoning is None


def test_effort_none_disables_thinking():
    req, _ = build_single_agent_llm_request(_ctx(reasoning_effort="none"))
    assert req.reasoning == {"enabled": False}


def test_live_budget_control_wins_over_effort():
    # SET_MAX_THINKING_TOKENS writes reasoning_max_tokens into tool_policy.metadata;
    # it must beat the static run-input effort tier.
    tp = ToolPolicyInput(metadata={"reasoning_max_tokens": 12000})
    req, _ = build_single_agent_llm_request(
        _ctx(reasoning_effort="high", tool_policy=tp)
    )
    assert req.reasoning == {"max_tokens": 12000}
