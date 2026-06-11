"""Tests for research/todo final-readiness contract."""

from __future__ import annotations

from agent_driver.runtime.research_session_contract import (
    DEEP_RESEARCH_PHASE_REVIEW,
    DEEP_RESEARCH_PHASE_WRITE,
    FINAL_READINESS_ALLOWED,
    FINAL_READINESS_REPAIR_NEEDED,
    REPAIR_CHILD_SYNTHESIS_PENDING,
    REPAIR_FINAL_MISSING_SOURCE_LINKS,
    REPAIR_MISSING_FETCHED_SOURCES,
    REPAIR_MISSING_RESEARCH_EVIDENCE,
    REPAIR_PARENT_REVIEW_PENDING,
    REPAIR_UNFINISHED_TODOS,
    build_research_session_contract,
    parent_review_actions_seen,
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


def test_deep_research_child_synthesis_pending_moves_phase_to_write() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[],
        child_synthesis_pending=True,
    )

    assert contract.model_dump()["deep_research"]["phase"] == DEEP_RESEARCH_PHASE_WRITE
    assert REPAIR_CHILD_SYNTHESIS_PENDING in contract.final_readiness.reasons


def test_deep_research_child_synthesis_with_report_moves_phase_to_review() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[],
        child_synthesis_pending=True,
        report_artifact_exists=True,
    )

    assert contract.model_dump()["deep_research"]["phase"] == DEEP_RESEARCH_PHASE_REVIEW


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


def test_research_contract_counts_hard_read_tools_as_verified_reads() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            _tool_result("source_read", url="https://example.com/a"),
            _tool_result("pdf_read", url="https://example.org/paper.pdf"),
        ],
        web_fetch_available=True,
        assistant_text="Sources: https://example.com/a https://example.org/paper.pdf",
    )

    assert contract.evidence.successful_fetches == 2
    assert contract.evidence.unique_domains == ("example.com", "example.org")
    assert [item["source_type"] for item in contract.source_ledger.verified_reads] == [
        "source_read",
        "pdf_read",
    ]
    assert contract.final_readiness.status == FINAL_READINESS_ALLOWED


def test_research_contract_preserves_hard_source_ladder_metadata() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            {
                "call": {
                    "tool_name": "pdf_read",
                    "tool_call_id": "pdf_1",
                    "args": {"url": "https://example.org/paper.pdf"},
                },
                "structured_output": {
                    "url": "https://example.org/paper.pdf",
                    "source_kind": "pdf",
                    "status": "verified",
                    "verified_text": True,
                    "text": "PDF evidence text",
                    "page_start": 2,
                    "page_end": 3,
                    "page_citations": [
                        {"page": 2, "url": "https://example.org/paper.pdf"},
                        {"page": 3, "url": "https://example.org/paper.pdf"},
                    ],
                },
            }
        ],
        assistant_text="[PDF](https://example.org/paper.pdf)",
    )

    row = contract.source_ledger.verified_reads[0]
    assert row["source_type"] == "pdf_read"
    assert row["source_kind"] == "pdf"
    assert row["verified_text"] is True
    assert row["page_start"] == 2
    assert row["page_end"] == 3
    assert len(row["page_citations"]) == 2
    assert row["content_sha256"]


def test_research_contract_rejects_partial_pdf_as_verified_evidence() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            {
                "call": {
                    "tool_name": "pdf_read",
                    "tool_call_id": "pdf_1",
                    "args": {"url": "https://example.org/scanned.pdf"},
                },
                "structured_output": {
                    "url": "https://example.org/scanned.pdf",
                    "source_kind": "pdf",
                    "status": "partial",
                    "verified_text": False,
                    "error": "text_extraction_unavailable",
                },
            }
        ],
        assistant_text="[PDF](https://example.org/scanned.pdf)",
    )

    assert contract.evidence.successful_fetches == 0
    assert contract.evidence.failed_fetches == 1
    assert contract.source_ledger.verified_reads == []
    assert contract.source_ledger.failed_reads[0]["status"] == "failed"
    assert contract.source_ledger.failed_reads[0]["source_kind"] == "pdf"


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


# Six verified reads across three domains — enough to clear the deep-parallel
# (hard) discovery floor of 6 fetches / 3 domains when rolled up into a parent.
_CHILD_VERIFIED_READS_6_3 = [
    {"url": "https://example.com/a", "domain": "example.com"},
    {"url": "https://example.com/b", "domain": "example.com"},
    {"url": "https://example.org/c", "domain": "example.org"},
    {"url": "https://example.org/d", "domain": "example.org"},
    {"url": "https://example.net/e", "domain": "example.net"},
    {"url": "https://example.net/f", "domain": "example.net"},
]


def test_research_contract_rolls_up_child_verified_reads() -> None:
    # A delegating parent that fetched nothing itself still satisfies the
    # source-verified contract once its children's verified reads roll up.
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[_tool_result("agent_tool")],
        assistant_text="See https://example.com/a and https://example.org/b",
        web_fetch_available=True,
        child_source_ledgers=[
            {
                "search_candidates": [{"url": "https://example.com/x"}],
                "verified_reads": _CHILD_VERIFIED_READS_6_3,
            }
        ],
    )

    assert contract.evidence.successful_fetches == 6
    assert set(contract.evidence.unique_domains) == {
        "example.com",
        "example.org",
        "example.net",
    }
    assert len(contract.source_ledger.verified_reads) == 6
    assert all(
        row.get("origin") == "child"
        for row in contract.source_ledger.verified_reads
    )
    assert REPAIR_MISSING_RESEARCH_EVIDENCE not in contract.final_readiness.reasons
    assert REPAIR_MISSING_FETCHED_SOURCES not in contract.final_readiness.reasons


def _path_result(tool_name: str, *, path: str) -> dict[str, object]:
    return {
        "call": {
            "tool_name": tool_name,
            "tool_call_id": f"call_{tool_name}",
            "args": {"path": path},
        },
        "structured_output": {},
    }


def test_parent_review_read_step_satisfied_by_any_research_artifact() -> None:
    # The forced read_file only pins the tool name, not the path, so the model
    # often reads research/sources.jsonl instead of research/report.md. Reading
    # any research artifact must satisfy the read-review step (the patch step
    # still requires the report itself).
    seen = parent_review_actions_seen(
        [_path_result("read_file", path="research/sources.jsonl")]
    )
    assert seen["read_file"] is True
    assert seen["file_patch"] is False

    # A patch of a non-report artifact does NOT satisfy the patch step.
    seen_patch = parent_review_actions_seen(
        [_path_result("file_patch", path="research/sources.jsonl")]
    )
    assert seen_patch["file_patch"] is False
    assert parent_review_actions_seen(
        [_path_result("file_patch", path="research/report.md")]
    )["file_patch"] is True


def test_deep_research_parent_review_pending_after_child_join() -> None:
    # Children fetched and rolled up, but the delegating parent has not done its
    # own verify+review pass yet — the run must not finalize on the draft stub.
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[_tool_result("agent_tool"), _tool_result("web_search")],
        planning_state={
            "run_id": "run_dr",
            "todos": [{"todo_id": "t1", "content": "Research", "status": "completed"}],
        },
        assistant_text="See https://example.com/a and https://example.org/c",
        web_fetch_available=True,
        report_artifact_exists=True,
        source_ledger_artifact_exists=True,
        child_source_ledgers=[{"verified_reads": _CHILD_VERIFIED_READS_6_3}],
    )

    assert contract.parent_review_pending is True
    assert REPAIR_PARENT_REVIEW_PENDING in contract.final_readiness.reasons
    assert contract.model_dump()["deep_research"]["phase"] == DEEP_RESEARCH_PHASE_REVIEW


def test_deep_research_parent_review_cleared_after_verify_and_review() -> None:
    # One parent verify-fetch + read_file/artifact_preview/file_patch of the
    # report clears the gate.
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=[
            _tool_result("agent_tool"),
            _tool_result("web_fetch", url="https://example.net/c"),
            _path_result("read_file", path="research/report.md"),
            _tool_result("artifact_preview"),
            _path_result("file_patch", path="research/report.md"),
        ],
        assistant_text="See https://example.com/a and https://example.net/c",
        web_fetch_available=True,
        child_source_ledgers=[
            {
                "verified_reads": [
                    {"url": "https://example.com/a", "domain": "example.com"},
                    {"url": "https://example.org/b", "domain": "example.org"},
                ]
            }
        ],
    )

    assert contract.parent_review_pending is False
    assert REPAIR_PARENT_REVIEW_PENDING not in contract.final_readiness.reasons


def test_parent_review_gate_scoped_to_deep_parallel() -> None:
    # The medium (source_verified_report) profile is unaffected by the gate.
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[_tool_result("web_search")],
        web_fetch_available=True,
        child_source_ledgers=[
            {
                "verified_reads": [
                    {"url": "https://example.com/a", "domain": "example.com"},
                    {"url": "https://example.org/b", "domain": "example.org"},
                ]
            }
        ],
    )

    assert contract.parent_review_pending is False
    assert REPAIR_PARENT_REVIEW_PENDING not in contract.final_readiness.reasons


def test_research_contract_child_rollup_dedupes_against_parent() -> None:
    # A page read by both the parent and a child counts once, and child rows
    # never lower the parent's own earned evidence.
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "source_verified_report",
        },
        tool_results=[
            _tool_result("web_search"),
            _tool_result("web_fetch", url="https://example.com/a"),
        ],
        web_fetch_available=True,
        child_source_ledgers=[
            {
                "verified_reads": [
                    {"url": "https://example.com/a", "domain": "example.com"},
                    {"url": "https://example.net/c", "domain": "example.net"},
                ]
            }
        ],
    )

    urls = {row["url"] for row in contract.source_ledger.verified_reads}
    assert urls == {"https://example.com/a", "https://example.net/c"}
    assert contract.evidence.successful_fetches == 2
    assert set(contract.evidence.unique_domains) == {"example.com", "example.net"}


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
        tool_results=_deep_parallel_fetch_results(),
        assistant_text="[A](https://example.com/a), [C](https://example.org/c)",
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
        "file_edit",
        "file_patch",
        "read_file",
        "artifact_list",
        "artifact_read",
        "artifact_preview",
        "todo_write",
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
    assert payload["next_allowed_tools"] == ["todo_write", "skill_tool", "skill_view"]


def test_deep_parallel_research_phase_allows_agent_tool_after_plan() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        planning_state={
            "run_id": "run_todo",
            "todos": [
                {
                    "todo_id": "discover",
                    "content": "Discover source families",
                    "status": "in_progress",
                }
            ],
        },
        tool_results=[],
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "discover"
    assert "agent_tool" in payload["next_allowed_tools"]


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
    assert "source_read" in payload["next_allowed_tools"]
    assert "pdf_read" in payload["next_allowed_tools"]
    assert "browser_read" in payload["next_allowed_tools"]


def _deep_parallel_fetch_results() -> list[dict[str, object]]:
    # Deep-parallel parents must clear a 6-fetch / 3-domain discovery floor.
    return [
        _tool_result("web_search"),
        _tool_result("web_fetch", url="https://example.com/a"),
        _tool_result("web_fetch", url="https://example.com/b"),
        _tool_result("web_fetch", url="https://example.org/c"),
        _tool_result("web_fetch", url="https://example.org/d"),
        _tool_result("web_fetch", url="https://example.net/e"),
        _tool_result("web_fetch", url="https://example.net/f"),
    ]


def test_deep_parallel_research_phase_final_after_report_and_verified_sources() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=_deep_parallel_fetch_results(),
        assistant_text="[A](https://example.com/a), [C](https://example.org/c)",
        report_artifact_exists=True,
        source_ledger_artifact_exists=True,
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "final"
    assert payload["next_allowed_tools"] == []
    assert payload["controller_state"]["final_handoff_ready"] is True


def test_deep_parallel_research_report_without_source_ledger_stays_in_review() -> None:
    contract = build_research_session_contract(
        task_contract={
            "requires_research": True,
            "research_depth": "deep_parallel_research",
        },
        tool_results=_deep_parallel_fetch_results(),
        assistant_text="[A](https://example.com/a), [C](https://example.org/c)",
        report_artifact_exists=True,
    )

    payload = contract.model_dump()["deep_research"]
    assert payload["phase"] == "review"
    assert payload["controller_state"]["source_ledger_required"] is True
    assert payload["controller_state"]["final_handoff_ready"] is False


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
