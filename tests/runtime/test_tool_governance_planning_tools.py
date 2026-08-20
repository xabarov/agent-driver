"""Governed executor tests for planning/todo/ask-user tools."""

from __future__ import annotations

import pytest

from agent_driver.contracts import (
    AgentRunInput,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    register_builtin_tools,
    register_planning_tool,
)
from tests.runtime.conftest import llm_request_with_planned_calls


@pytest.mark.asyncio
async def test_governed_executor_applies_todo_write_into_planning_state() -> None:
    """todo_write should update planning_state metadata through runtime flow."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="write todos",
        run_id="run_planning_todo_write",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="todo_write",
                    args={
                        "merge": False,
                        "todos": [
                            {"id": "t1", "content": "step", "status": "in_progress"}
                        ],
                    },
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is None
    assert result.envelopes[0].decision.value == "allow"
    structured = result.envelopes[0].structured_output
    assert isinstance(structured, dict)
    assert structured["applied_args"]["todo_items"][0]["id"] == "t1"


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_ask_user_question() -> None:
    """ask_user_question should produce clarification interrupt envelope."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="ask user",
        run_id="run_planning_ask_user",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="ask_user_question",
                    args={
                        "prompt": "Choose path",
                        "choices": [
                            {"id": "a", "label": "A"},
                            {"id": "b", "label": "B"},
                        ],
                    },
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is not None
    assert result.interrupt.reason.value == "clarification_required"
    assert result.interrupt.proposed_action["questions"][0]["question"] == "Choose path"
    assert result.envelopes[0].decision.value == "interrupt"


@pytest.mark.asyncio
async def test_governed_executor_interrupts_for_exit_plan_mode_approval() -> None:
    """exit_plan_mode_v2 with plan content should produce plan approval interrupt."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="approve plan",
        run_id="run_planning_exit_approval",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={
                        "reason": "ready",
                        "content": "1. Inspect\n2. Implement\n3. Verify",
                        "path": "/tmp/plan.md",
                        "requested_tools": ["file_write"],
                        "target_urls": ["file:///tmp"],
                    },
                    tool_call_id="call_plan",
                )
            ]
        )
    )
    result = await executor.execute(run_input, response)
    assert result.interrupt is not None
    assert result.interrupt.reason.value == "plan_approval_required"
    assert result.envelopes[0].decision.value == "interrupt"
    proposed = result.interrupt.proposed_action["plan_approval"]
    assert proposed["content_hash"]
    assert proposed["path"] == "/tmp/plan.md"
    assert result.interrupt.proposed_prompts[0].tool_name == "file_write"


@pytest.mark.asyncio
async def test_exit_plan_mode_blocks_requested_tools_outside_current_allowlist() -> None:
    """Approval plans may only request tools executable in this run."""
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _ok(_args):
        return {"summary": "ok"}

    registry.register(ToolManifest(name="lookup", description="Lookup"), _ok)
    registry.register(ToolManifest(name="nmap", description="Nmap"), _ok)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="approve plan",
        run_id="run_planning_exit_invalid_tool",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=["exit_plan_mode_v2", "lookup"],
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={
                        "reason": "ready",
                        "content": "1. Scan\n2. Report",
                        "requested_tools": ["nmap"],
                        "target_urls": ["https://lab.example/"],
                    },
                    tool_call_id="call_invalid_plan",
                )
            ]
        )
    )

    result = await executor.execute(run_input, response)

    assert result.interrupt is None
    assert result.envelopes[0].decision.value == "deny"
    assert result.envelopes[0].error is not None
    assert result.envelopes[0].error.code == "plan_requested_tools_unavailable"
    structured = result.envelopes[0].structured_output
    assert isinstance(structured, dict)
    assert structured["invalid_requested_tools"] == ["nmap"]
    assert structured["current_executable_tools"] == ["lookup"]


@pytest.mark.asyncio
async def test_exit_plan_mode_blocks_plan_text_that_mentions_unavailable_tools() -> None:
    """Approval-plan body may not smuggle known non-executable tools."""
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _ok(_args):
        return {"summary": "ok"}

    registry.register(ToolManifest(name="lookup", description="Lookup"), _ok)
    registry.register(ToolManifest(name="nuclei", description="Nuclei"), _ok)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="approve plan",
        run_id="run_planning_exit_invalid_text_tool",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=["exit_plan_mode_v2", "lookup"],
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={
                        "reason": "ready",
                        "content": "1. Lookup\n2. Run nuclei templates later\n3. Report",
                        "requested_tools": ["lookup"],
                        "target_urls": ["https://lab.example/"],
                    },
                    tool_call_id="call_invalid_plan_text",
                )
            ]
        )
    )

    result = await executor.execute(run_input, response)

    assert result.interrupt is None
    assert result.envelopes[0].decision.value == "deny"
    assert result.envelopes[0].error is not None
    assert result.envelopes[0].error.code == "plan_content_mentions_unavailable_tools"
    structured = result.envelopes[0].structured_output
    assert isinstance(structured, dict)
    assert structured["mentioned_unavailable_tools"] == ["nuclei"]
    assert structured["current_executable_tools"] == ["lookup"]


@pytest.mark.asyncio
async def test_exit_plan_mode_blocks_host_forbidden_plan_text_terms() -> None:
    """Hosts may reject approval-plan text that exceeds narrowed run contracts."""
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _ok(_args):
        return {"summary": "ok"}

    registry.register(ToolManifest(name="web_request_analyze", description="HTTP"), _ok)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="approve plan",
        run_id="run_planning_exit_forbidden_text",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=["exit_plan_mode_v2", "web_request_analyze"],
            metadata={"plan_content_forbidden_terms": ["OPTIONS", "POST"]},
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={
                        "reason": "ready",
                        "content": "Use web_request_analyze for GET/HEAD/OPTIONS checks.",
                        "requested_tools": ["web_request_analyze"],
                        "target_urls": ["https://lab.example/"],
                    },
                    tool_call_id="call_forbidden_plan_text",
                )
            ]
        )
    )

    result = await executor.execute(run_input, response)

    assert result.interrupt is None
    assert result.envelopes[0].decision.value == "deny"
    assert result.envelopes[0].error is not None
    assert result.envelopes[0].error.code == "plan_content_forbidden_terms"
    structured = result.envelopes[0].structured_output
    assert isinstance(structured, dict)
    assert structured["forbidden_terms"] == ["OPTIONS"]
    assert structured["retry_expected"] is True


@pytest.mark.asyncio
async def test_exit_plan_mode_keeps_valid_requested_tools_and_metadata() -> None:
    """A valid plan approval carries the current execution-tool truth."""
    registry = ToolRegistry()
    register_planning_tool(registry)

    async def _ok(_args):
        return {"summary": "ok"}

    registry.register(ToolManifest(name="lookup", description="Lookup"), _ok)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="approve plan",
        run_id="run_planning_exit_valid_tool",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            mode=ToolPolicyMode.ALLOW_TOOLS,
            allowed_tools=["exit_plan_mode_v2", "lookup"],
        ),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={
                        "reason": "ready",
                        "content": "1. Lookup\n2. Report",
                        "requested_tools": ["lookup"],
                        "target_urls": ["https://lab.example/"],
                    },
                    tool_call_id="call_valid_plan",
                )
            ]
        )
    )

    result = await executor.execute(run_input, response)

    assert result.interrupt is not None
    assert result.interrupt.proposed_prompts[0].tool_name == "lookup"
    assert result.interrupt.metadata["tool_ids"] == ["lookup"]
    approval = result.interrupt.proposed_action["plan_approval"]
    assert approval["metadata"]["tool_ids"] == ["lookup"]


@pytest.mark.asyncio
async def test_governed_executor_does_not_interrupt_for_plan_only_content() -> None:
    """A plan-only result has no execution permission to approve."""
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    executor = GovernedToolExecutor(registry=registry)
    run_input = AgentRunInput(
        input="write a plan only",
        run_id="run_planning_exit_plan_only",
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(
                    tool_name="exit_plan_mode_v2",
                    args={"content": "1. Inspect\n2. Design\n3. Verify"},
                    tool_call_id="call_plan_only",
                )
            ]
        )
    )

    result = await executor.execute(run_input, response)

    assert result.interrupt is None
