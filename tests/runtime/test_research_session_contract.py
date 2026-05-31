"""Tests for research/todo final-readiness contract."""

from __future__ import annotations

from agent_driver.runtime.research_session_contract import (
    FINAL_READINESS_ALLOWED,
    FINAL_READINESS_REPAIR_NEEDED,
    REPAIR_FINAL_MISSING_SOURCE_LINKS,
    REPAIR_MISSING_FETCHED_SOURCES,
    REPAIR_MISSING_RESEARCH_EVIDENCE,
    REPAIR_UNFINISHED_TODOS,
    build_research_session_contract,
)


def _tool_result(tool_name: str, *, url: str | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "call": {
            "tool_name": tool_name,
            "tool_call_id": f"call_{tool_name}",
            "args": {},
        },
        "structured_output": {},
    }
    if url is not None:
        payload["call"] = {
            "tool_name": tool_name,
            "tool_call_id": f"call_{tool_name}",
            "args": {"url": url},
        }
        payload["structured_output"] = {"url": url}
    return payload


def test_research_contract_requires_web_evidence() -> None:
    contract = build_research_session_contract(
        task_contract={"requires_research": True, "research_depth": "light_search"},
        tool_results=[],
    )

    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED
    assert contract.final_readiness.reasons == (REPAIR_MISSING_RESEARCH_EVIDENCE,)


def test_research_contract_requires_fetched_source_verified_evidence() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[_tool_result("web_search")],
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED
    assert REPAIR_MISSING_FETCHED_SOURCES in contract.final_readiness.reasons


def test_research_contract_requires_final_links_after_verified_fetches() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
            _tool_result("web_fetch", url="https://example.org/b"),
        ],
        assistant_text="Итог без ссылок.",
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED
    assert contract.final_readiness.reasons == (REPAIR_FINAL_MISSING_SOURCE_LINKS,)


def test_research_contract_blocks_unfinished_visible_todos() -> None:
    contract = build_research_session_contract(
        task_contract={"requires_research": False},
        tool_results=[],
        planning_state={
            "run_id": "run_todo",
            "todos": [
                {
                    "todo_id": "one",
                    "content": "Do one",
                    "status": "completed",
                },
                {
                    "todo_id": "two",
                    "content": "Do two",
                    "status": "pending",
                },
            ],
        },
    )

    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED
    assert contract.final_readiness.reasons == (REPAIR_UNFINISHED_TODOS,)


def test_research_contract_allows_verified_research_with_links_and_done_todos() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
            _tool_result("web_fetch", url="https://example.org/b"),
        ],
        planning_state={
            "run_id": "run_todo",
            "todos": [
                {
                    "todo_id": "one",
                    "content": "Do one",
                    "status": "completed",
                }
            ],
        },
        assistant_text="[A](https://example.com/a), [B](https://example.org/b)",
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.final_readiness.reasons == ()
