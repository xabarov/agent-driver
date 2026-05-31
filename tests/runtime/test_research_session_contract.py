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


def test_research_contract_requires_fetch_when_user_requested_open_url() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "light_search",
            "fetch_required": True,
        },
        tool_results=[_tool_result("web_search")],
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED
    assert REPAIR_MISSING_FETCHED_SOURCES in contract.final_readiness.reasons


def test_research_contract_allows_fetch_required_after_one_fetch() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "light_search",
            "fetch_required": True,
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
        ],
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED


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


def test_research_contract_allows_final_answer_to_cover_synthesis_todo() -> None:
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
                    "todo_id": "search",
                    "content": "Search sources",
                    "status": "completed",
                },
                {
                    "todo_id": "summary",
                    "content": "Сводка полученной информации с указанием источников",
                    "status": "in_progress",
                },
            ],
        },
        assistant_text=(
            "Итоговый отчет с обобщением найденных данных и ссылками на "
            "[A](https://example.com/a), [B](https://example.org/b). "
            "Текст достаточно длинный, чтобы считаться реальным финальным "
            "ответом, а не коротким progress update."
        ),
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.final_readiness.reasons == ()


def test_research_contract_can_allow_final_deliverable_todo_before_answer() -> None:
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
                    "todo_id": "summary",
                    "content": "Синтез и оформление ответа",
                    "status": "in_progress",
                },
            ],
        },
        web_fetch_available=True,
        enforce_final_source_links=False,
        allow_final_deliverable_todos=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.final_readiness.reasons == ()


def test_meaningful_sourced_final_answer_covers_research_process_todos() -> None:
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
                    "todo_id": "search",
                    "content": "Поискать информацию о моделях",
                    "status": "pending",
                },
                {
                    "todo_id": "read",
                    "content": "Изучить основные источники",
                    "status": "in_progress",
                },
            ],
        },
        assistant_text=(
            "Итоговый отчет: fork-join queue разбивает работу на параллельные "
            "подзадачи и ждет их объединения; это применимо к параллельным "
            "сервисам и сетевым расчетам задержки. Источники: "
            "[A](https://example.com/a), [B](https://example.org/b). "
            "Этого текста достаточно, чтобы считаться содержательным финальным "
            "ответом, а не коротким progress update."
        ),
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.final_readiness.reasons == ()


def test_research_contract_allows_final_answer_to_cover_output_todo() -> None:
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
                    "todo_id": "research",
                    "content": "Собрать источники",
                    "status": "completed",
                },
                {
                    "todo_id": "output",
                    "content": "Подготовить краткий вывод со ссылками",
                    "status": "in_progress",
                },
            ],
        },
        assistant_text=(
            "Краткий вывод: FastAPI лучше подходит для API-first сервисов, "
            "а Django удобнее для приложений с ORM и админкой. Подробности "
            "подтверждаются источниками [A](https://example.com/a) и "
            "[B](https://example.org/b), поэтому этот ответ закрывает "
            "финальный пункт плана."
        ),
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.final_readiness.reasons == ()


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


def test_source_ledger_does_not_launder_candidates() -> None:
    """Search candidates should not count as verified source evidence."""
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            {
                "call": {"tool_name": "web_search", "tool_call_id": "s1"},
                "structured_output": {
                    "results": [
                        {
                            "url": "https://candidate.example/a",
                            "title": "Candidate",
                        }
                    ]
                },
            },
            _tool_result("web_fetch", url="https://verified.example/a"),
        ],
        assistant_text="See https://verified.example/a",
    )

    payload = contract.model_dump()
    assert payload["source_ledger"]["search_candidates"][0]["source_type"] == (
        "web_search"
    )
    assert payload["source_ledger"]["verified_reads"][0]["url"] == (
        "https://verified.example/a"
    )
    assert payload["source_ledger"]["assistant_links"][0]["url"] == (
        "https://verified.example/a"
    )
    assert contract.final_readiness.status == FINAL_READINESS_REPAIR_NEEDED


def test_deep_parallel_research_uses_readiness_contract_and_mode_payload() -> None:
    """Deep research mode keeps ResearchSessionContract as readiness authority."""
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
            _tool_result("web_fetch", url="https://example.org/b"),
        ],
        assistant_text="[A](https://example.com/a), [B](https://example.org/b)",
    )

    payload = contract.model_dump()
    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert payload["deep_research"]["mode"] == "deep_parallel_research"
    assert payload["deep_research"]["final_readiness_authority"] == (
        "ResearchSessionContract"
    )
    assert payload["deep_research"]["phase"] == "write"
    assert payload["deep_research"]["next_allowed_tools"] == [
        "file_write",
        "read_file",
        "artifact_list",
    ]


def test_deep_parallel_research_phase_starts_with_plan_tools() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[],
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "plan"
    assert payload["next_allowed_tools"] == ["todo_write"]


def test_deep_parallel_research_phase_verifies_after_search() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        planning_state={
            "run_id": "run_todo",
            "todos": [
                {
                    "todo_id": "search",
                    "content": "Search sources",
                    "status": "in_progress",
                }
            ],
        },
        tool_results=[_tool_result("web_search")],
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "verify"
    assert "web_fetch" in payload["next_allowed_tools"]


def test_deep_parallel_research_phase_final_after_report_and_verified_sources() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
            _tool_result("web_fetch", url="https://example.org/b"),
        ],
        assistant_text="[A](https://example.com/a), [B](https://example.org/b)",
        report_artifact_exists=True,
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "final"
    assert payload["next_allowed_tools"] == []


def test_deep_parallel_research_treats_blocked_fetches_as_fallback() -> None:
    """Blocked HTTP fetches should not masquerade as verified source reads."""
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[
            _tool_result("web_search"),
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "blocked_a",
                    "args": {"url": "https://example.com/blocked-a"},
                },
                "structured_output": {
                    "url": "https://example.com/blocked-a",
                    "status_code": 403,
                    "blocked": True,
                },
            },
            {
                "call": {
                    "tool_name": "web_fetch",
                    "tool_call_id": "blocked_b",
                    "args": {"url": "https://example.org/blocked-b"},
                },
                "structured_output": {
                    "url": "https://example.org/blocked-b",
                    "status_code": 403,
                    "blocked": True,
                },
            },
        ],
        assistant_text=(
            "Источники заблокировали чтение: https://example.com/blocked-a и "
            "https://example.org/blocked-b. Отчет явно помечает этот caveat."
        ),
        web_fetch_available=True,
    )

    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED
    assert contract.fetch_fallback_required is True
    assert contract.evidence.successful_fetches == 0
    assert contract.evidence.failed_fetches == 2
    assert len(contract.source_ledger.blocked_reads) == 2
