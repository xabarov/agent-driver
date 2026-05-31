"""Tool-choice repair for research contract continuations."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.runtime.single_agent.steps import _maybe_build_continuation_transition


def _context(
    *,
    tool_results: list[dict[str, object]],
    planning_state: dict[str, object] | None = None,
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
            **(
                {"planning_state": planning_state} if planning_state is not None else {}
            ),
        },
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
