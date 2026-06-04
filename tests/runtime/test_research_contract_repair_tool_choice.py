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


def _gate_context(
    tool_name: str, args: dict[str, object] | None = None
) -> ToolGateContext:
    return ToolGateContext(
        tool_name=tool_name,
        args=args or {},
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


def test_contract_repair_forces_parent_patch_after_captured_report() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": (
                "file_write",
                "file_patch",
                "read_file",
                "artifact_preview",
            ),
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "last_update_kind": "capture",
            },
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
        "name": "file_patch",
    }
    assert context.metadata["deep_research_parent_synthesis_required"] == {
        "tool": "file_patch",
        "path": "research/report.md",
    }


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
    assert (
        context.metadata["deep_research_parent_synthesis_gate"]["blocked_tool"]
        == "glob_search"
    )


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_parent_search_after_child_handoff() -> None:
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

    assert gate is not None
    web_search = await gate(_gate_context("web_search"))
    assert isinstance(web_search, ToolGateDeny)
    assert "deep_research_parent_synthesis_gate denied" in web_search.reason
    agent_tool = await gate(_gate_context("agent_tool"))
    assert isinstance(agent_tool, ToolGateDeny)
    assert "deep_research_parent_synthesis_gate denied" in agent_tool.reason


@pytest.mark.asyncio
async def test_initial_subagent_gate_recovers_when_contract_disappears_after_todo() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "todo_write",
                    "tool_call_id": "todo_1",
                    "args": {},
                }
            }
        ],
        metadata={
            "effective_tool_names": (
                "agent_tool",
                "glob_search",
                "web_search",
                "todo_write",
            ),
        },
    )
    context.run_input.tool_policy.metadata = {}

    gate = _tool_gate_for_context(context)

    assert gate is not None
    glob_search = await gate(_gate_context("glob_search"))
    assert isinstance(glob_search, ToolGateDeny)
    assert "deep_research_initial_subagent_gate denied" in glob_search.reason
    agent_tool = await gate(_gate_context("agent_tool"))
    assert isinstance(agent_tool, ToolGateAllow)


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_artifact_list_before_report() -> None:
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

    assert gate is not None
    artifact_list = await gate(_gate_context("artifact_list"))
    assert isinstance(artifact_list, ToolGateDeny)
    assert (
        context.metadata["deep_research_parent_synthesis_gate"]["blocked_tool"]
        == "artifact_list"
    )
    file_write = await gate(_gate_context("file_write"))
    assert isinstance(file_write, ToolGateAllow)
    web_fetch = await gate(_gate_context("web_fetch"))
    assert isinstance(web_fetch, ToolGateDeny)


@pytest.mark.asyncio
async def test_parent_synthesis_gate_allows_bounded_child_candidate_fetch() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child found https://example.com/source",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://example.com/source"})
    )
    assert isinstance(web_fetch, ToolGateAllow)
    unknown_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://unrelated.example/page"})
    )
    assert isinstance(unknown_fetch, ToolGateDeny)


@pytest.mark.asyncio
async def test_parent_synthesis_gate_allows_parent_search_candidate_fetch() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "web_search",
                    "tool_call_id": "search_1",
                    "args": {"query": "fork join queue"},
                },
                "structured_output": {
                    "results": [
                        {
                            "title": "Fork-join queue",
                            "url": "https://example.org/fork-join",
                        }
                    ]
                },
            }
        ],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes without direct urls",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://example.org/fork-join"})
    )
    assert isinstance(web_fetch, ToolGateAllow)
    unknown_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://unrelated.example/page"})
    )
    assert isinstance(unknown_fetch, ToolGateDeny)


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_parent_search_without_direct_urls() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes without direct urls",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_search = await gate(_gate_context("web_search", {"query": "fork join queue"}))
    assert isinstance(web_search, ToolGateDeny)
    assert "deep_research_parent_synthesis_gate denied" in web_search.reason

    context.metadata["tool_results"] = [
        {
            "call": {
                "tool_name": "web_search",
                "tool_call_id": "search_1",
                "args": {"query": "fork join queue"},
            }
        }
    ]
    second_search = await gate(
        _gate_context("web_search", {"query": "fork join queueing model"})
    )
    assert isinstance(second_search, ToolGateDeny)


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_fetch_after_verify_limit() -> None:
    context = _context(
        tool_results=[
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": f"fetch_{index}",
                    "args": {"url": f"https://example.com/source-{index}"},
                }
            }
            for index in range(3)
        ],
        metadata={
            "subagent_runs": [{"run_id": "child_1"}],
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child found https://example.com/source",
            },
        },
    )

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://example.com/source"})
    )
    assert isinstance(web_fetch, ToolGateDeny)


@pytest.mark.asyncio
async def test_terminal_handoff_gate_blocks_hidden_tools_after_artifacts_ready() -> (
    None
):
    context = _context(
        tool_results=[],
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "report_size_bytes": 1024,
                "source_ledger_exists": True,
                "source_ledger_path": "research/sources.jsonl",
                "source_ledger_size_bytes": 128,
            },
            "deep_research_child_synthesis": {
                "pending": True,
                "summary": "child notes",
            },
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_depth": "deep_parallel_research",
        "research_profile": "medium",
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    decision = await gate(_gate_context("file_read"))
    assert isinstance(decision, ToolGateDeny)
    assert "deep_research_terminal_handoff_gate denied" in decision.reason
    assert context.metadata["deep_research_terminal_handoff_gate"] == {
        "blocked_tool": "file_read",
        "allowed_tools": [],
        "reason": "artifacts_ready_for_final_handoff",
    }


@pytest.mark.asyncio
async def test_artifact_repair_gate_blocks_fetch_when_report_exists_without_ledger() -> (
    None
):
    context = _context(
        tool_results=[],
        metadata={
            "deep_research_artifacts": {
                "report_exists": True,
                "report_path": "research/report.md",
                "report_size_bytes": 1024,
            },
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_depth": "deep_parallel_research",
        "research_profile": "medium",
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_fetch = await gate(
        _gate_context("web_fetch", {"url": "https://example.com/source"})
    )
    assert isinstance(web_fetch, ToolGateDeny)
    assert "deep_research_artifact_repair_gate denied" in web_fetch.reason
    wrong_file_write = await gate(
        _gate_context("file_write", {"path": "research/report.md"})
    )
    assert isinstance(wrong_file_write, ToolGateDeny)
    assert (
        context.metadata["deep_research_artifact_repair_gate"]["required_path"]
        == "research/sources.jsonl"
    )
    file_write = await gate(
        _gate_context("file_write", {"path": "research/sources.jsonl"})
    )
    assert isinstance(file_write, ToolGateAllow)


@pytest.mark.asyncio
async def test_parent_gate_reevaluates_artifact_state_between_batch_calls() -> None:
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
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_depth": "deep_parallel_research",
        "research_profile": "medium",
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    first = await gate(_gate_context("file_write"))
    assert isinstance(first, ToolGateAllow)
    context.metadata["deep_research_artifacts"] = {
        "report_exists": True,
        "report_path": "research/report.md",
        "report_size_bytes": 1024,
    }
    second = await gate(_gate_context("read_file", {"path": "research/report.md"}))
    assert isinstance(second, ToolGateDeny)
    assert "deep_research_artifact_repair_gate denied" in second.reason


@pytest.mark.asyncio
async def test_parent_synthesis_gate_blocks_second_child_even_with_explicit_budget() -> None:
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
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_depth": "deep_parallel_research",
        "research_profile": "hard",
        "max_subagent_requests": 2,
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_search = await gate(_gate_context("web_search"))
    assert isinstance(web_search, ToolGateDeny)
    agent_tool = await gate(_gate_context("agent_tool"))
    assert isinstance(agent_tool, ToolGateDeny)


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


@pytest.mark.asyncio
async def test_medium_deep_research_gate_requires_initial_subagent() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": ("agent_tool", "web_search", "todo_write"),
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_profile": "medium",
        "max_subagent_requests": 2,
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_search = await gate(_gate_context("web_search"))
    assert isinstance(web_search, ToolGateDeny)
    assert "deep_research_initial_subagent_gate denied" in web_search.reason
    agent_tool = await gate(_gate_context("agent_tool"))
    assert isinstance(agent_tool, ToolGateAllow)


@pytest.mark.asyncio
async def test_app_metadata_deep_research_gate_requires_initial_subagent() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": ("agent_tool", "web_search", "todo_write"),
        },
    )
    context.run_input.app_metadata = {
        "research_mode": "deep",
        "research_profile": "medium",
        "research_depth": "deep_parallel_research",
    }
    context.run_input.tool_policy.metadata.pop("task_contract")

    gate = _tool_gate_for_context(context)

    assert gate is not None
    web_search = await gate(_gate_context("web_search"))
    assert isinstance(web_search, ToolGateDeny)
    assert "deep_research_initial_subagent_gate denied" in web_search.reason


@pytest.mark.asyncio
async def test_source_verified_report_does_not_enable_initial_subagent_gate() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": ("agent_tool", "web_search", "todo_write"),
        },
    )

    assert _tool_gate_for_context(context) is None


@pytest.mark.asyncio
async def test_medium_deep_research_recovery_gate_blocks_skill_view_loop() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": ("agent_tool", "skill_view", "todo_write"),
            "deep_research_initial_subagent_recovery": {
                "tool": "agent_tool",
                "reason": "initial_subagent_gate_denied",
            },
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_profile": "medium",
        "max_subagent_requests": 2,
    }

    gate = _tool_gate_for_context(context)

    assert gate is not None
    skill_view = await gate(_gate_context("skill_view"))
    assert isinstance(skill_view, ToolGateDeny)
    assert "deep_research_initial_subagent_gate denied" in skill_view.reason
    agent_tool = await gate(_gate_context("agent_tool"))
    assert isinstance(agent_tool, ToolGateAllow)


def test_contract_repair_prioritizes_initial_subagent_recovery() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": (
                "agent_tool",
                "web_search",
                "web_fetch",
                "todo_write",
            ),
            "deep_research_initial_subagent_recovery": {
                "tool": "agent_tool",
                "reason": "initial_subagent_gate_denied",
            },
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_profile": "medium",
        "max_subagent_requests": 2,
    }

    result = _maybe_build_continuation_transition(context)

    assert result is not None
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "agent_tool",
    }
    assert context.metadata["deep_research_initial_subagent_recovery"] == {
        "tool": "agent_tool",
        "reason": "contract_repair_before_initial_subagent",
    }


@pytest.mark.asyncio
async def test_light_deep_research_gate_allows_direct_web_search() -> None:
    context = _context(
        tool_results=[],
        metadata={
            "effective_tool_names": ("agent_tool", "web_search", "todo_write"),
        },
    )
    context.run_input.tool_policy.metadata["task_contract"] = {
        "requires_research": True,
        "research_mode": "deep",
        "research_profile": "light",
        "max_subagent_requests": 0,
    }

    gate = _tool_gate_for_context(context)

    assert gate is None


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
