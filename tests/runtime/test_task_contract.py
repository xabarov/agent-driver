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


def test_research_contract_is_lightweight() -> None:
    contract = build_chat_task_contract("найди в интернете свежие источники")
    assert contract is not None
    assert contract["kind"] == "research"
    assert "Uses data tools" in contract["acceptance_criteria"][0]


def test_simple_chat_has_no_contract() -> None:
    assert build_chat_task_contract("привет") is None
