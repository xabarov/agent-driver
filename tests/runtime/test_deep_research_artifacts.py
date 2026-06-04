from __future__ import annotations

from pathlib import Path

from agent_driver.contracts import AgentRunInput, ToolPolicyInput
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime.research_artifacts import (
    CLAIMS_RELATIVE_PATH,
    REPORT_RELATIVE_PATH,
    SOURCE_LEDGER_RELATIVE_PATH,
    deep_research_claims_artifact_exists,
    deep_research_source_ledger_artifact_exists,
    ensure_deep_research_report_artifact_metadata,
    maybe_capture_deep_research_draft,
    persist_deep_research_claims_matrix,
    persist_deep_research_source_ledger,
)
from agent_driver.runtime.single_agent.lifecycle.steps import (
    _maybe_build_continuation_transition,
)
from agent_driver.runtime.single_agent.types import RunContext


def _context(tmp_path: Path, *, text: str = "draft", hard: bool = False) -> RunContext:
    run_input = AgentRunInput(
        input="research",
        run_id="run_deep_artifacts",
        thread_id="thread_deep_artifacts",
        agent_id="agent.test",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(
            metadata={
                "deep_research_mode": {
                    "enabled": True,
                    "research_profile": "hard" if hard else "medium",
                },
                "task_contract": {
                    "research_depth": "deep_parallel_research",
                    "research_profile": "hard" if hard else "medium",
                },
            }
        ),
        app_metadata={
            "chat_mode": True,
            "workspace_cwd": str(tmp_path),
            "deep_research_inline_answer_max_chars": 1_000,
        },
    )
    return RunContext(
        run_input=run_input,
        identifiers={
            "run_id": "run_deep_artifacts",
            "attempt_id": "attempt_test",
        },
        metadata={
            "next_step": "finalize",
            "step_count": 0,
            "llm_step_count": 1,
            "tool_calls": 0,
            "workspace_cwd": str(tmp_path),
        },
        llm_response=LlmResponse(
            message=ChatMessage(role=ChatRole.ASSISTANT, content=text),
            finish_reason=LlmFinishReason.STOP,
            provider="fake",
            model="fake",
        ),
    )


def test_deep_research_default_capture_threshold_is_artifact_first(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.run_input.app_metadata.pop("deep_research_inline_answer_max_chars", None)
    draft = "Deep research draft.\n" + ("section text\n" * 150)

    payload = maybe_capture_deep_research_draft(context, draft)

    assert payload is not None
    assert payload["captured_text_chars"] == len(draft)
    assert (tmp_path / REPORT_RELATIVE_PATH).is_file()


def test_deep_research_long_inline_draft_is_captured_to_report(tmp_path: Path) -> None:
    context = _context(tmp_path)
    draft = "Deep research draft.\n" + ("section text\n" * 200)

    payload = maybe_capture_deep_research_draft(context, draft)

    assert payload is not None
    report = tmp_path / REPORT_RELATIVE_PATH
    assert report.read_text(encoding="utf-8") == draft
    assert context.metadata["deep_research_artifacts"]["report_exists"] is True
    assert context.metadata["deep_research_artifacts"]["captured_long_answers"] == 1


def test_deep_research_child_run_does_not_capture_parent_report(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    context.run_input.app_metadata["subagent_origin"] = "child"
    draft = "Deep research child notes.\n" + ("source note\n" * 200)

    payload = maybe_capture_deep_research_draft(context, draft)
    ledger_payload = persist_deep_research_source_ledger(
        context,
        {
            "verified_reads": [
                {
                    "url": "https://example.com/paper",
                    "domain": "example.com",
                    "source_type": "web_fetch",
                }
            ]
        },
    )

    assert payload is None
    assert ledger_payload is None
    assert not (tmp_path / REPORT_RELATIVE_PATH).exists()
    assert not (tmp_path / SOURCE_LEDGER_RELATIVE_PATH).exists()


def test_deep_research_capture_does_not_overwrite_existing_report(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    report = tmp_path / REPORT_RELATIVE_PATH
    report.parent.mkdir(parents=True)
    report.write_text("existing report", encoding="utf-8")

    payload = maybe_capture_deep_research_draft(context, "new draft\n" * 200)

    assert payload is None
    assert report.read_text(encoding="utf-8") == "existing report"
    assert context.metadata["deep_research_artifacts"]["last_update_reason"] == (
        "existing_report"
    )


def test_deep_research_existing_report_is_observed_for_metadata(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    report = tmp_path / REPORT_RELATIVE_PATH
    report.parent.mkdir(parents=True)
    report.write_text("report written by file_write", encoding="utf-8")

    payload = ensure_deep_research_report_artifact_metadata(context)

    assert payload is not None
    assert payload["report_path"] == REPORT_RELATIVE_PATH
    assert payload["report_size_bytes"] > 0
    assert payload["captured_long_answers"] == 0


def test_deep_research_source_ledger_is_persisted_to_jsonl(tmp_path: Path) -> None:
    context = _context(tmp_path)
    ledger = {
        "verified_reads": [
            {
                "url": "https://example.com/paper",
                "domain": "example.com",
                "source_type": "web_fetch",
                "title": "Paper",
            }
        ],
        "search_candidates": [
            {
                "url": "https://example.org/candidate",
                "domain": "example.org",
                "source_type": "web_search",
                "rank": 1,
            }
        ],
        "failed_reads": [],
        "blocked_reads": [],
        "assistant_links": [],
    }

    payload = persist_deep_research_source_ledger(context, ledger)

    assert payload is not None
    assert payload["path"] == SOURCE_LEDGER_RELATIVE_PATH
    assert payload["created"] is True
    assert payload["record_count"] == 2
    source_ledger = tmp_path / SOURCE_LEDGER_RELATIVE_PATH
    content = source_ledger.read_text(encoding="utf-8")
    assert '"ledger_section": "verified_reads"' in content
    assert '"ledger_section": "search_candidates"' in content
    artifacts = context.metadata["deep_research_artifacts"]
    assert artifacts["source_ledger_exists"] is True
    assert artifacts["source_ledger_path"] == SOURCE_LEDGER_RELATIVE_PATH
    assert artifacts["source_ledger_record_count"] == 2
    assert deep_research_source_ledger_artifact_exists(context) is True


def test_deep_research_source_ledger_exists_from_workspace_file(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path)
    source_ledger = tmp_path / SOURCE_LEDGER_RELATIVE_PATH
    source_ledger.parent.mkdir(parents=True)
    source_ledger.write_text('{"url": "https://example.com"}\n', encoding="utf-8")

    assert deep_research_source_ledger_artifact_exists(context) is True


def test_deep_research_claims_matrix_is_hard_only(tmp_path: Path) -> None:
    context = _context(tmp_path)
    ledger = {
        "verified_reads": [{"url": "https://example.com/a", "source_type": "web_fetch"}]
    }

    assert persist_deep_research_claims_matrix(context, ledger) is None
    assert not (tmp_path / CLAIMS_RELATIVE_PATH).exists()


def test_deep_research_claims_matrix_persists_hard_audit_rows(
    tmp_path: Path,
) -> None:
    context = _context(tmp_path, hard=True)
    ledger = {
        "verified_reads": [
            {
                "url": "https://example.com/a",
                "source_type": "source_read",
                "source_kind": "url",
            }
        ],
        "blocked_reads": [
            {
                "url": "https://example.org/b",
                "source_type": "browser_read",
                "source_kind": "rendered_page",
                "fallback_reason": "source_read blocked",
            }
        ],
        "failed_reads": [],
    }

    payload = persist_deep_research_claims_matrix(context, ledger)

    assert payload is not None
    assert payload["path"] == CLAIMS_RELATIVE_PATH
    assert payload["record_count"] == 2
    assert payload["verified_count"] == 1
    # A blocked source read is an *inaccessible* source, not a false claim, so
    # it must not count as "unsupported" (which would auto-fail the hard gate
    # on every paywalled candidate). It is tracked separately for follow-up.
    assert payload["unsupported_count"] == 0
    assert payload["inaccessible_count"] == 1
    claims_path = tmp_path / CLAIMS_RELATIVE_PATH
    content = claims_path.read_text(encoding="utf-8")
    assert '"claim_id": "verified_1"' in content
    assert '"claim_id": "blocked_1"' in content
    assert '"status": "verified"' in content
    assert '"status": "inaccessible"' in content
    assert '"status": "unsupported"' not in content
    artifacts = context.metadata["deep_research_artifacts"]
    assert artifacts["claims_exists"] is True
    assert artifacts["claims_record_count"] == 2
    assert artifacts["claims_verified_count"] == 1
    assert artifacts["claims_unsupported_count"] == 0
    assert artifacts["claims_inaccessible_count"] == 1
    assert deep_research_claims_artifact_exists(context) is True


def test_contract_repair_uses_captured_report_instead_of_full_prompt(
    tmp_path: Path,
) -> None:
    draft = "Deep research draft with unfinished todos.\n" + ("body\n" * 300)
    context = _context(tmp_path, text=draft)
    context.metadata["planning_state"] = {
        "run_id": "run_deep_artifacts",
        "todos": [
            {
                "todo_id": "todo_1",
                "content": "Search more sources",
                "status": "pending",
            }
        ],
    }

    transition = _maybe_build_continuation_transition(context)

    assert transition is not None
    report = tmp_path / REPORT_RELATIVE_PATH
    assert report.read_text(encoding="utf-8") == draft
    protocol = context.metadata["protocol_messages"]
    assistant_messages = [
        item
        for item in protocol
        if isinstance(item, dict) and item.get("role") == ChatRole.ASSISTANT.value
    ]
    assert len(assistant_messages) == 1
    assert "captured to research/report.md" in assistant_messages[0]["content"]
    assert "body\nbody\nbody" not in assistant_messages[0]["content"]
    assert context.metadata["tool_choice_override"] == {
        "type": "tool",
        "name": "todo_write",
    }
