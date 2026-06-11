from __future__ import annotations

from agent_driver.runtime.task_contract import build_chat_task_contract


def test_task_contract_respects_no_search_instruction() -> None:
    contract = build_chat_task_contract(
        "Собери по памяти 3 факта о Fender Jazzmaster, без поиска в интернете"
    )

    assert contract is None


def test_task_contract_keeps_positive_research_instruction() -> None:
    contract = build_chat_task_contract(
        "Найди в интернете источник про Fender Jazzmaster и дай итог"
    )

    assert contract is not None
    assert contract["kind"] == "research"
    assert contract["requires_research"] is True
