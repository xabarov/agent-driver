"""Tests for lightweight chat task contracts."""

from __future__ import annotations

from agent_driver.runtime.task_contract import (
    build_chat_task_contract,
    render_task_contract_reminder,
)


def test_deliverable_contract_discourages_replanning() -> None:
    contract = build_chat_task_contract("напиши реферат по Fender, не план")
    assert contract is not None
    assert contract["kind"] == "deliverable"
    assert any(
        "Final response contains the requested deliverable" in item
        for item in contract["acceptance_criteria"]
    )
    assert "Restarting the plan" in contract["out_of_scope"][0]

    reminder = render_task_contract_reminder(contract)
    assert reminder is not None
    assert "task_contract_active (deliverable)" in reminder
    assert "Out of scope" in reminder


def test_deliverable_contract_preserves_research_requirement() -> None:
    contract = build_chat_task_contract(
        "составь план поиска информации в интернете и написания реферата"
    )
    assert contract is not None
    assert contract["kind"] == "deliverable"
    assert contract["requires_research"] is True
    assert any(
        "use available web/data tools" in item
        for item in contract["acceptance_criteria"]
    )

    reminder = render_task_contract_reminder(contract)
    assert reminder is not None
    assert "Research requirement" in reminder


def test_research_contract_is_lightweight() -> None:
    contract = build_chat_task_contract("найди в интернете свежие источники")
    assert contract is not None
    assert contract["kind"] == "research"
    assert "Uses data tools" in contract["acceptance_criteria"][0]


def test_plan_only_contract_does_not_force_research() -> None:
    contract = build_chat_task_contract(
        "составь только план поиска информации по истории Fender, без реферата"
    )

    assert contract is not None
    assert contract["kind"] == "plan"
    assert contract["requires_research"] is False
    assert "Does not perform web/data research" in contract["acceptance_criteria"][1]


def test_simple_chat_has_no_contract() -> None:
    assert build_chat_task_contract("привет") is None
