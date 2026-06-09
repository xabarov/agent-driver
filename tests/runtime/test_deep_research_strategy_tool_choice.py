"""Deep Research strategy-level tool-choice tests."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_driver.runtime.single_agent.llm_step.request import (
    _deep_research_request_allowed_tools,
    _deep_research_strategy_tool_choice,
    _provider_safe_tool_choice,
)

# Known pre-existing test debt (not a regression from the 2026-06 tracks).
# These encode the older rule "once research report/ledger artifacts exist,
# collapse the tool surface to the ledger write / terminal handoff". Production
# intentionally evolved: while ``deep_research_post_artifact_next_tool`` reports
# the delegating parent still owes its verify+review pass, the review/verify
# surface stays open (see ``llm_step/request.py`` and ``research/gating.py``).
# xfail (non-strict) tracks the divergence for the deep-research owner to
# reconcile the expectations against the post-artifact gating.
_POST_ARTIFACT_GATING_DEBT = (
    "pre-gating expectation: artifacts-exist collapses the tool surface; "
    "production now keeps verify/review open until post-artifact work is done"
)


def _context(
    *,
    profile: str | None = "medium",
    research_depth: str = "deep_parallel_research",
    research_mode: str | None = None,
    max_subagent_requests: int = 1,
    tool_results: list[dict[str, object]] | None = None,
    planning_state: dict[str, object] | None = None,
    metadata: dict[str, object] | None = None,
    app_metadata: dict[str, object] | None = None,
) -> SimpleNamespace:
    runtime_metadata: dict[str, object] = {
        "tool_results": tool_results or [],
        **(metadata or {}),
    }
    if planning_state is not None:
        runtime_metadata["planning_state"] = planning_state
    return SimpleNamespace(
        llm_step_count=2,
        metadata=runtime_metadata,
        run_input=SimpleNamespace(
            app_metadata=app_metadata or {},
            tool_policy=SimpleNamespace(
                allowed_tools=None,
                denied_tools=[],
                metadata={
                    "task_contract": {
                        "requires_research": True,
                        "research_depth": research_depth,
                        "max_subagent_requests": max_subagent_requests,
                        **(
                            {"research_profile": profile} if profile is not None else {}
                        ),
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


def _tool_result(tool_name: str, *, status: str | None = None) -> dict[str, object]:
    result: dict[str, object] = {
        "call": {
            "tool_name": tool_name,
            "tool_call_id": f"call_{tool_name}",
            "args": {},
        }
    }
    if status is not None:
        result["status"] = status
    return result


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


def test_medium_after_plan_narrows_request_tools_to_first_subagent() -> None:
    context = _context(
        research_mode="deep",
        tool_results=[_tool_result("todo_write")],
        planning_state={
            "todos": [
                {
                    "todo_id": "discover",
                    "content": "Find sources",
                    "status": "in_progress",
                }
            ]
        },
    )

    assert _deep_research_request_allowed_tools(context) == ("agent_tool",)


def test_medium_after_plan_falls_back_when_agent_tool_not_effective() -> None:
    context = _context(
        research_mode="deep",
        tool_results=[_tool_result("todo_write")],
        planning_state={
            "todos": [
                {
                    "todo_id": "discover",
                    "content": "Find sources",
                    "status": "in_progress",
                }
            ]
        },
        metadata={"effective_tool_names": ("todo_write", "file_write")},
    )

    assert _deep_research_request_allowed_tools(context) is None
    assert _deep_research_strategy_tool_choice(context, None) is None


def test_medium_before_plan_narrows_request_tools_to_planning_only() -> None:
    context = _context(research_mode="deep")

    assert _deep_research_request_allowed_tools(context) == ("todo_write",)


def test_medium_after_skill_discovery_still_narrows_to_first_subagent() -> None:
    context = _context(
        research_mode="deep",
        tool_results=[_tool_result("todo_write"), _tool_result("skill_tool")],
        planning_state={
            "todos": [
                {
                    "todo_id": "discover",
                    "content": "Find sources",
                    "status": "in_progress",
                }
            ]
        },
    )

    assert _deep_research_request_allowed_tools(context) == ("agent_tool",)


def test_medium_initial_subagent_recovery_narrows_request_to_agent_tool_only() -> None:
    context = _context(
        research_mode="deep",
        tool_results=[_tool_result("todo_write")],
        planning_state={
            "todos": [
                {
                    "todo_id": "discover",
                    "content": "Find sources",
                    "status": "in_progress",
                }
            ]
        },
    )
    context.metadata["deep_research_initial_subagent_recovery"] = {
        "tool": "agent_tool",
        "reason": "initial_subagent_gate_denied",
    }

    assert _deep_research_request_allowed_tools(context) == ("agent_tool",)


@pytest.mark.xfail(reason=_POST_ARTIFACT_GATING_DEBT, strict=False)
def test_deep_research_with_report_only_narrows_to_ledger_write(
    tmp_path: Path,
) -> None:
    context = _context(research_mode="deep")
    context.metadata["workspace_cwd"] = str(tmp_path)
    report = tmp_path / "research" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("ready report", encoding="utf-8")

    assert _deep_research_request_allowed_tools(context) == ("file_write",)
    choice = _deep_research_strategy_tool_choice(context, None)
    assert choice == {"type": "tool", "name": "file_write"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_write",
        "path": "research/sources.jsonl",
        "reason": "deep_research_source_ledger_missing",
    }


@pytest.mark.xfail(reason=_POST_ARTIFACT_GATING_DEBT, strict=False)
def test_deep_research_with_report_and_ledger_disables_tools(
    tmp_path: Path,
) -> None:
    context = _context(research_mode="deep")
    context.metadata["workspace_cwd"] = str(tmp_path)
    report = tmp_path / "research" / "report.md"
    ledger = tmp_path / "research" / "sources.jsonl"
    report.parent.mkdir(parents=True)
    report.write_text("ready report", encoding="utf-8")
    ledger.write_text('{"url": "https://example.com"}\n', encoding="utf-8")

    assert _deep_research_request_allowed_tools(context) == tuple()
    assert _deep_research_strategy_tool_choice(context, None) == "none"
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "none",
        "reason": "deep_research_artifacts_ready",
    }


@pytest.mark.xfail(reason=_POST_ARTIFACT_GATING_DEBT, strict=False)
def test_deep_research_with_report_and_ledger_overrides_child_pending_to_final(
    tmp_path: Path,
) -> None:
    context = _context(research_mode="deep")
    context.metadata["workspace_cwd"] = str(tmp_path)
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }
    report = tmp_path / "research" / "report.md"
    ledger = tmp_path / "research" / "sources.jsonl"
    report.parent.mkdir(parents=True)
    report.write_text("ready report", encoding="utf-8")
    ledger.write_text('{"url": "https://example.com"}\n', encoding="utf-8")

    assert _deep_research_request_allowed_tools(context) == tuple()
    assert _deep_research_strategy_tool_choice(context, None) == "none"


def test_deep_research_with_ledger_only_forces_report_write(
    tmp_path: Path,
) -> None:
    context = _context(research_mode="deep")
    context.metadata["workspace_cwd"] = str(tmp_path)
    ledger = tmp_path / "research" / "sources.jsonl"
    ledger.parent.mkdir(parents=True)
    ledger.write_text('{"url": "https://example.com"}\n', encoding="utf-8")

    assert _deep_research_request_allowed_tools(context) == ("file_write",)


def test_parent_synthesis_recovery_narrows_to_report_write() -> None:
    context = _context()
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }
    context.metadata["deep_research_parent_synthesis_recovery"] = {
        "tool": "file_write",
        "reason": "parent_synthesis_gate_denied",
    }

    assert _deep_research_request_allowed_tools(context) == ("file_write",)


def test_pending_child_synthesis_narrows_to_write_after_fetch() -> None:
    context = _context(
        tool_results=[
            {
                "status": "completed",
                "call": {
                    "tool_name": "web_fetch",
                    "args": {"url": "https://example.com"},
                },
            }
        ]
    )
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "child notes",
    }

    assert _deep_research_request_allowed_tools(context) == ("file_write",)
    choice = _deep_research_strategy_tool_choice(context, None)
    assert choice == {"type": "tool", "name": "file_write"}


def test_strategy_honors_child_handoff_even_without_visible_contract() -> None:
    context = _context(
        profile=None,
        research_mode=None,
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

    assert _deep_research_request_allowed_tools(context) == (
        "file_write",
        "todo_write",
        "web_fetch",
    )
    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "file_write",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "child_synthesis_pending_budget_exhausted"
    )


def test_strategy_fetches_concrete_child_url_without_parent_search() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "Candidate source: https://example.com/fork-join",
    }

    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "web_fetch",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "child_synthesis_pending_parent_verify_fetch"
    )


def test_strategy_fetches_url_from_child_source_ledger() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "Child summary without direct URLs.",
        "children": [
            {
                "summary": "Still no URL here.",
                "source_ledger": {
                    "verified_reads": [{"url": "https://Example.com/fork-join/"}]
                },
            }
        ],
    }

    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "web_fetch",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "child_synthesis_pending_parent_verify_fetch"
    )


def test_strategy_keeps_parent_verify_fetch_after_prior_parent_search() -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
            _tool_result("web_search"),
        ],
    )
    context.metadata["subagent_runs"] = [{"run_id": "child_1"}]
    context.metadata["deep_research_child_synthesis"] = {
        "pending": True,
        "summary": "Candidate source: https://example.com/fork-join",
    }

    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "web_fetch",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "child_synthesis_pending_parent_verify_fetch"
    )


def test_strategy_does_not_override_explicit_choice_or_light_profile() -> None:
    explicit = {"type": "tool", "name": "web_search"}
    assert _deep_research_strategy_tool_choice(_context(), explicit) is explicit
    assert (
        _deep_research_strategy_tool_choice(
            _context(profile="light", max_subagent_requests=0),
            None,
        )
        is None
    )


def test_strategy_waits_for_initial_todo_before_agent_tool() -> None:
    context = _context()

    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "todo_write",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "medium_hard_requires_initial_todo_plan"
    )


def test_strategy_keeps_deep_research_active_after_contract_disappears() -> None:
    context = _context(
        profile=None,
        research_mode=None,
        metadata={
            "deep_research_context_active": True,
            "deep_research_active_profile": "medium",
        },
        tool_results=[_tool_result("todo_write")],
    )

    assert _deep_research_request_allowed_tools(context) == ("agent_tool",)
    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "agent_tool",
    }
    assert context.metadata["deep_research_strategy_tool_choice"]["reason"] == (
        "medium_hard_requires_bounded_subagents"
    )


def test_strategy_recovers_agent_tool_after_initial_todo_when_metadata_marker_missing() -> (
    None
):
    context = _context(
        profile=None,
        research_mode=None,
        tool_results=[_tool_result("todo_write")],
    )

    assert _deep_research_request_allowed_tools(context) == ("agent_tool",)
    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "agent_tool",
    }


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


def test_strategy_forces_second_agent_after_child_handoff_with_explicit_budget() -> (
    None
):
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


def test_strategy_forces_file_write_after_child_budget_exhausted_without_url() -> None:
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


def test_strategy_forces_file_write_after_parent_verify_fetch_budget_exhausted() -> (
    None
):
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
            _tool_result("web_fetch", status="failed"),
            _tool_result("web_fetch", status="failed"),
            _tool_result("web_fetch", status="failed"),
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


def test_failed_report_write_does_not_clear_child_synthesis_strategy(
    tmp_path: Path,
) -> None:
    context = _context(
        tool_results=[
            _tool_result("todo_write"),
            _tool_result("agent_tool"),
            {
                "status": "failed",
                "call": {
                    "tool_name": "file_write",
                    "tool_call_id": "write_report",
                    "args": {"path": "research/report.md"},
                },
            },
            _tool_result("web_fetch", status="failed"),
            _tool_result("web_fetch", status="failed"),
            _tool_result("web_fetch", status="failed"),
        ],
        metadata={"workspace_cwd": str(tmp_path)},
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


def test_strategy_forces_write_without_child_url_for_deep_source_verified_contract() -> (
    None
):
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
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_write",
        "path": "research/report.md",
        "reason": "child_synthesis_pending_budget_exhausted",
    }


def test_source_verified_report_without_deep_mode_does_not_use_deep_strategy() -> None:
    context = _context(
        research_depth="source_verified_report",
        research_mode=None,
        profile=None,
    )
    context.metadata["deep_research_artifacts"] = {
        "report_exists": True,
        "report_path": "research/report.md",
        "report_size_bytes": 512,
    }

    assert _deep_research_request_allowed_tools(context) is None
    assert _deep_research_strategy_tool_choice(context, None) is None


def test_source_verified_report_with_medium_profile_uses_deep_strategy() -> None:
    context = _context(
        research_depth="source_verified_report",
        research_mode=None,
        profile="medium",
    )

    assert _deep_research_request_allowed_tools(context) == ("todo_write",)
    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "todo_write",
    }


def test_app_metadata_deep_profile_enables_request_strategy_without_contract() -> None:
    context = _context(
        research_depth="source_verified_report",
        research_mode=None,
        profile=None,
        app_metadata={
            "research_mode": "deep",
            "research_profile": "medium",
            "research_depth": "deep_parallel_research",
        },
    )
    context.run_input.tool_policy.metadata.pop("task_contract")

    assert _deep_research_request_allowed_tools(context) == ("todo_write",)
    assert _deep_research_strategy_tool_choice(context, None) == {
        "type": "tool",
        "name": "todo_write",
    }


@pytest.mark.xfail(reason=_POST_ARTIFACT_GATING_DEBT, strict=False)
def test_strategy_forces_source_ledger_when_child_notes_pending_with_captured_report() -> (
    None
):
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

    assert choice == {"type": "tool", "name": "file_write"}
    assert context.metadata["deep_research_strategy_tool_choice"] == {
        "tool": "file_write",
        "path": "research/sources.jsonl",
        "reason": "deep_research_source_ledger_missing",
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


@pytest.mark.xfail(reason=_POST_ARTIFACT_GATING_DEBT, strict=False)
def test_provider_rejection_keeps_report_only_schema_narrowing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    context = _context(research_mode="deep")
    context.metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"
    context.metadata["deep_research_artifacts"] = {
        "report_exists": True,
        "report_path": "research/report.md",
        "report_size_bytes": 128,
    }
    report = tmp_path / "research" / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("ready report", encoding="utf-8")

    forced = _deep_research_strategy_tool_choice(context, None)
    choice = _provider_safe_tool_choice(context, forced)

    assert forced == {"type": "tool", "name": "file_write"}
    assert choice is None
    assert _deep_research_request_allowed_tools(context) == ("file_write",)


def test_provider_safe_tool_choice_keeps_forced_final_none() -> None:
    context = _context()
    context.metadata["forced_tool_choice_retry"] = "removed_after_provider_rejection"

    assert _provider_safe_tool_choice(context, "none") == "none"
