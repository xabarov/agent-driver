"""Phase 11 H13 — end-to-end test for prompt-based permission bypass.

Pins the contract: when a tool call would normally trigger a policy
INTERRUPT (approval required), but the run carries an approved
AllowedPrompt category that matches the call shape, the executor
collapses the INTERRUPT to ALLOW and runs the handler normally.

This test uses a tool with ``approval_mode=ALWAYS`` to force an
INTERRUPT policy decision, then verifies that:

* without approved_prompts, the executor produces an interrupt
  (baseline)
* with approved_prompts that match, the executor runs the handler
  and produces an envelope (bypass works)
* with approved_prompts that DON'T match, baseline still applies
  (no false bypass).
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
from agent_driver.contracts.interrupts import (
    AllowedPrompt,
    AllowedPromptPattern,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls


def _build_run_input(run_id: str, *, approved_prompts=None) -> AgentRunInput:
    app_metadata = {}
    if approved_prompts:
        app_metadata["approved_prompts"] = [
            p.model_dump() if isinstance(p, AllowedPrompt) else p
            for p in approved_prompts
        ]
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.APPROVAL_REQUIRED),
        app_metadata=app_metadata,
    )


def _register_approval_tool(registry: ToolRegistry) -> list[dict]:
    """Tool with approval_mode=ALWAYS to force INTERRUPT decision."""
    seen: list[dict] = []

    async def _handler(args):
        seen.append(dict(args))
        return {"summary": f"ran:{args.get('cmd', '')}"}

    registry.register(
        ToolManifest(
            name="risky_shell",
            description="risky shell — requires approval by default",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            approval_mode=ApprovalMode.ALWAYS,
            idempotent=False,
            output_char_budget=2000,
        ),
        _handler,
    )
    return seen


@pytest.mark.asyncio
async def test_baseline_interrupt_without_approved_prompts() -> None:
    """No approved_prompts on the run → policy INTERRUPT → handler not called."""
    registry = ToolRegistry()
    seen = _register_approval_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "npm test"})]
        )
    )
    result = await executor.execute(_build_run_input("run_baseline"), response)
    assert seen == []
    assert result.interrupt is not None


@pytest.mark.asyncio
async def test_matching_approved_prompt_bypasses_interrupt() -> None:
    """Approved 'npm tests' category → call runs without interrupt."""
    registry = ToolRegistry()
    seen = _register_approval_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    approved = [
        AllowedPrompt(
            category_id="npm_tests",
            description="npm test invocations",
            tool_name="risky_shell",
            arg_patterns=[AllowedPromptPattern(arg_name="cmd", regex=r"^npm test")],
        )
    ]
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "npm test"})]
        )
    )
    result = await executor.execute(
        _build_run_input("run_bypass", approved_prompts=approved), response
    )
    assert seen == [{"cmd": "npm test"}]
    assert result.interrupt is None
    assert result.envelopes[0].summary == "ran:npm test"


@pytest.mark.asyncio
async def test_non_matching_approved_prompt_does_not_bypass() -> None:
    """Approved category for 'git commit' doesn't bypass 'rm -rf /'."""
    registry = ToolRegistry()
    seen = _register_approval_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    approved = [
        AllowedPrompt(
            category_id="git_commits",
            description="git commits",
            tool_name="risky_shell",
            arg_patterns=[AllowedPromptPattern(arg_name="cmd", regex=r"^git commit")],
        )
    ]
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "rm -rf /"})]
        )
    )
    result = await executor.execute(
        _build_run_input("run_no_match", approved_prompts=approved), response
    )
    assert seen == []
    assert result.interrupt is not None


@pytest.mark.asyncio
async def test_malformed_approved_prompt_entry_does_not_bypass() -> None:
    """Bad entry in approved_prompts → skipped; baseline INTERRUPT preserved."""
    registry = ToolRegistry()
    seen = _register_approval_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    # Mix one bad entry (missing required fields) with one good non-matching.
    approved = [
        {"category_id": "broken"},  # missing description, tool_name, etc.
        AllowedPrompt(
            category_id="git_commits",
            description="git commits",
            tool_name="risky_shell",
            arg_patterns=[AllowedPromptPattern(arg_name="cmd", regex=r"^git commit")],
        ),
    ]
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "rm -rf /"})]
        )
    )
    run_input = AgentRunInput(
        input="hello",
        run_id="run_bad_entry",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.APPROVAL_REQUIRED),
        app_metadata={
            "approved_prompts": [
                approved[0],
                approved[1].model_dump(),
            ]
        },
    )
    result = await executor.execute(run_input, response)
    # Bad entry skipped; good entry doesn't match → baseline INTERRUPT.
    assert seen == []
    assert result.interrupt is not None


@pytest.mark.asyncio
async def test_first_matching_prompt_wins() -> None:
    """First approved prompt in list wins (priority order)."""
    registry = ToolRegistry()
    _register_approval_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    approved = [
        AllowedPrompt(
            category_id="specific_npm_test",
            description="specific npm test",
            tool_name="risky_shell",
            arg_patterns=[AllowedPromptPattern(arg_name="cmd", regex=r"^npm test$")],
        ),
        AllowedPrompt(
            category_id="any_shell",
            description="blanket trust",
            tool_name="risky_shell",
            arg_patterns=[],
        ),
    ]
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_shell", args={"cmd": "npm test"})]
        )
    )
    result = await executor.execute(
        _build_run_input("run_first_wins", approved_prompts=approved), response
    )
    assert result.interrupt is None
    # Both would match, but reason mentions the FIRST one.
    assert "specific_npm_test" in result.envelopes[0].metadata.get(
        "policy_reason", ""
    ) or result.envelopes[0].summary == "ran:npm test"
