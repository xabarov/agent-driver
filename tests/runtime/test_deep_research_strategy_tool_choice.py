"""Deep Research strategy-level tool-choice tests."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.runtime.single_agent.llm_step.request import (
    _deep_research_request_allowed_tools,
    _deep_research_strategy_tool_choice,
    _provider_safe_tool_choice,
)


def _context(
    *,
    profile: str = "medium",
    research_depth: str = "deep_parallel_research",
    research_mode: str | None = None,
    max_subagent_requests: int = 1,
    tool_results: list[dict[str, object]] | None = None,
    planning_state: dict[str, object] | None = None,
) -> SimpleNamespace:
    metadata: dict[str, object] = {"tool_results": tool_results or []}
    if planning_state is not None:
        metadata["planning_state"] = planning_state
    return SimpleNamespace(
        llm_step_count=2,
        metadata=metadata,
        run_input=SimpleNamespace(
            app_metadata={},
            tool_policy=SimpleNamespace(
                allowed_tools=None,
                denied_tools=[],
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": research_depth,
                        "research_profile": profile,
                        "max_subagent_requests": max_subagent_requests,
                        **(
                            {"research_mode": research_mode}
                            if research_mode is not None
                            else {}
                        ),
                    }
                },
            ),
        ),
    )


def _tool_result(tool_name: str) -> dict[str, object]:
    return {
        "call": {
            "tool_name": tool_name,
            "tool_call_id": f"call_{tool_name}",
            "args": {},
        }
    }


def test_medium_strategy_forces_agent_tool_after_initial_plan() -> None:
    context = _context(
        planning_state={
            "todos": [
                {"todo_id": "discover", "content": "Find sources", "status": "pending"}
            ]
        }
    )

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "agent_tool"}
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "medium_hard_requires_bounded_subagents"
    )


def test_pending_child_synthesis_narrows_request_tools_before_report() -> None:
    context = _context()
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }

    assert _deep_research_request_allowed_tools(context) == (
        "file_write",
        "todo_write",
        "web_fetch",
    )


def test_strategy_does_not_override_explicit_choice_or_light_profile() -> None:
    explicit = {"type": "tool", "name": "web_search"}
    assert _deep_research_strategy_tool_choice(_context(), explicit) is explicit
    assert _deep_research_strategy_tool_choice(
        _context(profile="light", max_subagent_requests=0),
        None,
    ) is None


def test_strategy_waits_for_initial_todo_before_agent_tool() -> None:
    context = _context()

    assert _deep_research_strategy_tool_choice(context, None) is None


def test_strategy_forces_file_write_after_agent_and_discovery_budget() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
            _tool_result("web_search"),
            _tool_result("web_search"),
            _tool_result("web_fetch"),
            _tool_result("web_fetch"),
        ]
    )

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "file_write"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_write",
        "path": "research/report.md",
        "reason": "deep_research_discovery_budget_reached",
    }


def test_strategy_forces_second_agent_after_child_handoff_with_explicit_budget() -> None:
    context = _context(
        max_subagent_requests=2,
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "agent_tool"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "agent_tool",
        "reason": "child_synthesis_pending_with_remaining_subagent_budget",
    }


def test_strategy_forces_file_write_after_child_budget_exhausted() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "file_write"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_write",
        "path": "research/report.md",
        "reason": "child_synthesis_pending_budget_exhausted",
    }


def test_strategy_forces_file_write_for_deep_source_verified_contract() -> None:
    context = _context(
        research_depth="source_verified_report",
        research_mode="deep",
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "file_write"}
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "child_synthesis_pending_budget_exhausted"
    )


def test_strategy_forces_patch_when_child_notes_pending_with_captured_report() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }
    context.metadata["deep_research_artifacts"] = {
        "report_exists": True,
        "report_path": "research/report.md",
        "last_update_kind": "capture",
    }

    choice = _deep_research_strategy_tool_choice(context, None)

    assert choice == {"type": "tool", "name": "file_patch"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_patch",
        "path": "research/report.md",
        "reason": "child_synthesis_pending_budget_exhausted",
    }


def test_strategy_respects_denied_tools() -> None:
    context = _context(
        planning_state={
            "todos": [
                {"todo_id": "discover", "content": "Find sources", "status": "pending"}
            ]
        }
    )
    context.run_input.tool_policy.denied_tools = ["agent_tool", "file_write"]

    assert _deep_research_strategy_tool_choice(context, None) is None


def test_provider_safe_tool_choice_disables_rejected_named_forcing() -> None:
    context = _context()
    context.metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"

    choice = _provider_safe_tool_choice(context, {"type": "tool", "name": "file_write"})

    assert choice is None
    assert context.metadata["forced_tool_choice_disabled"] == (
        "provider_rejected_named_tool_choice"
    )


def test_provider_safe_tool_choice_keeps_forced_final_none() -> None:
    context = _context()
    context.metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"

    assert _provider_safe_tool_choice(context, "none") == "none"
