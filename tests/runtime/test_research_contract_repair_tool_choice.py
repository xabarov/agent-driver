"""Tool-choice repair for research contract continuations."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.lifecycle.steps import (
    _maybe_build_continuation_transition,
    _tool_gate_for_context,
)
from agent_driver.runtime.tool_gate import ToolGateAllow, ToolGateContext, ToolGateDeny


def _context(
    *,
    tool_results: list[dict[str, object]],
    planning_state: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        llm_response=SimpleNamespace(
            message=ChatMessage(
                role=ChatRole.ASSISTANT,
                content="Готово: я составил план, но пока без источников.",
            )
        ),
        run_input=SimpleNamespace(
            input="исследуй fork-join очереди",
            messages=[],
            app_metadata={},
            tool_policy=SimpleNamespace(
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": "source_verified_report",
                    }
                },
                allowed_tools=None,
                denied_tools=[],
            ),
        ),
        metadata={
            "tool_results": tool_results,
            "effective_tool_names": ("web_search", "web_fetch", "todo_write"),
            **(metadata or {}),
            **(
                {"planning_state": planning_state} if planning_state is not None else {}
            ),
        },
        tool_gate=None,
    )


def _gate_context(tool_name: str) -> ToolGateContext:
    return ToolGateContext(
        tool_name=tool_name,
        args={},
        run_id="run",
        thread_id="thread",
        agent_id="agent",
        risk="low",
        side_effect="read",
        current_tool_calls=3,
    )


def test_contract_repair_forces_web_search_when_no_research_evidence() -> None:
    context = _context(tool_results=[])

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert result.next_step == "llm_call"
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_search",
    }


def test_contract_repair_forces_parent_file_write_after_child_synthesis() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": (
                "web_search",
                "web_fetch",
                "todo_write",
                "file_write",
                "artifact_preview",
            ),
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child note: https://example.com/source",
            },
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_depth": "deep_parallel_research",
    }

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "file_write",
    }
    assert context.metadata["deep_research_parent_synthesis_required"] == {
        "tool": "file_write",
        "path": "research/report.md",
    }
    messages = context.metadata["protocol_messages"]
    assert "joined child research notes" in messages[-1]["content"]
    assert "child note: https://example.com/source" in messages[-1]["content"]


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_discovery_after_child_handoff() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}, {"run_id": "child_2"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is not None
    decision = await gate(_gate_context("glob_search"))
    assert isinstance(decision, ToolGateDeny)
    assert "deep_research_parent_synthesis_gate denied" in decision.reason
    assert context.metadata["deep_research_parent_synthesis_gate"][
        "blocked_tool"
    ] == "glob_search"


@pytest.mark.asyncio
async def test_parent_synthesis_gate_allows_second_medium_child_before_lock() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is None


@pytest.mark.asyncio
async def test_parent_synthesis_gate_allows_write_and_preserves_existing_gate() -> None:
    seen: list[str] = []

    async def existing_gate(gate_context: ToolGateContext):
        seen.append(gate_context.tool_name)
        return ToolGateAllow(reason="existing")

    context = _context(
        tool_results=[],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}, {"run_id": "child_2"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes",
            },
        },
    )
    context.tool_gate = existing_gate

    gate = _tool_gate_for_context(context)

    assert gate is not None
    decision = await gate(_gate_context("file_write"))
    assert isinstance(decision, ToolGateAllow)
    assert decision.reason == "existing"
    assert seen == ["file_write"]


def test_contract_repair_forces_web_fetch_after_search_only_evidence() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "web_search",
                    "tool_call_id": "call_search",
                    "args": {"query": "fork join queues"},
                }
            }
        ]
    )

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_fetch",
    }


def test_contract_repair_forces_web_search_for_source_diversity() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "web_search",
                    "tool_call_id": "call_search",
                    "args": {"query": "fork join queues"},
                }
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "call_fetch_a",
                    "args": {"url": "https://example.com/a"},
                },
                "structured_output": {"url": "https://example.com/a"},
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "call_fetch_b",
                    "args": {"url": "https://example.com/b"},
                },
                "structured_output": {"url": "https://example.com/b"},
            },
        ]
    )

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "web_search",
    }


def test_contract_repair_forces_final_answer_when_research_is_done() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "web_search",
                    "tool_call_id": "call_search",
                    "args": {"query": "fork join queues"},
                }
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "call_fetch_a",
                    "args": {"url": "https://example.com/a"},
                },
                "structured_output": {"url": "https://example.com/a"},
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "call_fetch_b",
                    "args": {"url": "https://example.org/b"},
                },
                "structured_output": {"url": "https://example.org/b"},
            },
        ],
        planning_state={
            "run_id": "run_todo",
            "todos": [
                {"todo_id": "search", "content": "Search", "status": "pending"},
            ],
        },
    )

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert context.metadata["tool_choice_override"] == "none"
    assert context.metadata["force_final_answer"] is True


def test_contract_repair_allows_second_turn_when_reason_changes() -> None:
    context = _context(tool_results=[])
    first = _maybe_build_continuation_transition(context)
    assert first is not None

    context.llm_response = SimpleNamespace(
        message=ChatMessage(
            role=ChatRole.ASSISTANT,
            content="Теперь есть источники, но todo еще не закрыт.",
        )
    )
    context.metadata["tool_results"] = [
        {
            "call": {
                "tool_name": "web_search",
                "tool_call_id": "call_search",
                "args": {"query": "fork join queues"},
            }
        },
        {
            "call": {
                "tool_name": "web_fetch",
                "tool_call_id": "call_fetch_a",
                "args": {"url": "https://example.com/a"},
            },
            "structured_output": {"url": "https://example.com/a"},
        },
        {
            "call": {
                "tool_name": "web_fetch",
                "tool_call_id": "call_fetch_b",
                "args": {"url": "https://example.org/b"},
            },
            "structured_output": {"url": "https://example.org/b"},
        },
    ]
    context.metadata["planning_state"] = {
        "run_id": "run_todo",
        "todos": [
            {"todo_id": "search", "content": "Search", "status": "pending"},
        ],
    }

    second = _maybe_build_continuation_transition(context)

    assert second is not None
    assert context.metadata["contract_repair_nudge_count"] == 2
    assert context.metadata["tool_choice_override"] == "none"
