"""Playwright concept smokes for chat-demo agent behavior.

Run against an already running frontend dev server:

    CHAT_DEMO_URL=http://localhost:5174 \
      ./.uv-bootstrap/bin/python \
      examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py

The script routes chat SSE/interrupt endpoints in the browser, so it verifies
the real React UI without depending on a live provider response.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, Route, expect, sync_playwright

BASE_URL = os.environ.get("CHAT_DEMO_URL", "http://localhost:5174")
ARTIFACT_DIR = Path(
    os.environ.get("CHAT_DEMO_SCREENSHOT_DIR", "/tmp/chat-demo-concepts")
)


def sse_event(seq: int, event: str, data: dict[str, Any] | None = None) -> str:
    payload = {
        "schema_version": "1.0",
        "stream_id": f"run_test:{seq}",
        "run_id": "run_test",
        "attempt_id": "attempt_test",
        "seq": seq,
        "event": event,
        "source": "runtime_event",
        "data": data or {},
    }
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


def fulfill_sse(route: Route, body: str, *, run_id: str = "run_test") -> None:
    route.fulfill(
        status=200,
        headers={
            "content-type": "text/event-stream; charset=utf-8",
            "x-run-id": run_id,
        },
        body=body,
    )


def fulfill_json(route: Route, payload: dict[str, Any]) -> None:
    route.fulfill(
        status=200,
        headers={"content-type": "application/json"},
        body=json.dumps(payload, ensure_ascii=False),
    )


def trace_summary_payload(
    *,
    verdict: str = "pass",
    terminal_event: str | None = "run_completed",
    tool_names: list[str] | None = None,
    interrupts: list[str] | None = None,
    research_required: bool = False,
    research_tools: list[str] | None = None,
    planning_verdict: str | None = None,
    failures: dict[str, bool] | None = None,
) -> dict[str, Any]:
    flags = {
        "stuck_on_interrupt": False,
        "missing_terminal_event": False,
        "run_failed_or_cancelled": False,
        "missing_required_research_evidence": False,
        "progress_only_final": False,
        "text_form_tool_call": False,
        "fabricated_planning": False,
    }
    flags.update(failures or {})
    names = tool_names or []
    return {
        "run_id": "run_test",
        "verdict": verdict,
        "terminal_event": terminal_event,
        "llm_calls": 1,
        "tool_calls": len(names),
        "tool_names": names,
        "research": {
            "required": research_required,
            "tools_used": research_tools or [],
        },
        "planning": {
            "verdict": planning_verdict,
            "planning_tool_calls": sum(
                1 for name in names if name in {"todo_write", "planning_state_update"}
            ),
            "data_tool_calls": sum(
                1
                for name in names
                if name not in {"todo_write", "planning_state_update"}
            ),
            "snapshots": 1 if planning_verdict else 0,
            "latest_snapshot": None,
        },
        "interrupts": interrupts or [],
        "continuation_reason": None,
        "failures": flags,
        "notes": [],
    }


def route_trace_summary(page: Page, payload: dict[str, Any]) -> None:
    page.route(
        "**/api/chat/runs/run_test/trace-summary",
        lambda route: fulfill_json(route, payload),
    )


def deep_research_state_payload() -> dict[str, Any]:
    return {
        "runId": "run_test",
        "sessionId": "session_dr",
        "researchMode": "deep",
        "profile": "medium",
        "profileSource": "user_selected",
        "phase": "final",
        "phaseSource": "trace_summary",
        "readiness": "needs_verified_sources",
        "todos": {"done": 3, "total": 3, "current": None, "stale": False},
        "artifacts": {
            "report": {
                "path": "research/report.md",
                "kind": "research_report",
                "sizeBytes": 2048,
                "modifiedAt": "2026-06-04T00:00:00Z",
                "lifecycle": "captured_inline",
                "previewAvailable": True,
            },
            "sourceLedger": {
                "path": "research/sources.jsonl",
                "kind": "research_sources",
                "sizeBytes": 512,
                "modifiedAt": "2026-06-04T00:00:00Z",
                "lifecycle": "updated",
                "previewAvailable": True,
            },
            "claims": None,
        },
        "sources": {
            "verified": 0,
            "candidates": 1,
            "blocked": 1,
            "failed": 0,
            "distinctDomains": 2,
            "requiredVerified": 1,
            "qualityStatus": "candidate_only",
            "qualityOk": False,
            "rows": [
                {
                    "status": "candidate",
                    "title": "Candidate source",
                    "url": "https://example.com/candidate",
                    "domain": "example.com",
                    "reason": None,
                },
                {
                    "status": "blocked",
                    "title": "Blocked source",
                    "url": "https://blocked.example/source",
                    "domain": "blocked.example",
                    "reason": "fetch_denied",
                },
            ],
        },
        "subagents": {
            "totalChildren": 1,
            "runningChildren": 0,
            "completedChildren": 1,
            "failedChildren": 0,
            "duplicatedQueries": 0,
            "toolNames": ["web_search", "web_fetch"],
            "summaryChars": 120,
            "sourceRecords": 2,
        },
        "metrics": {
            "promptTokens": 1000,
            "completionTokens": 500,
            "totalTokens": 1500,
            "webSearchCount": 1,
            "webFetchCount": 1,
            "reportFullWriteCount": 1,
            "reportPatchCount": 0,
            "longChatBeforeReportChars": 0,
        },
        "warnings": ["quality_candidate_only"],
        "trace": {
            "runId": "run_test",
            "verdict": "pass",
            "terminalEvent": "run_completed",
            "failureFlags": [],
        },
    }


def route_deep_research_state(page: Page) -> None:
    page.route(
        "**/api/chat/runs/run_test/deep-research-state",
        lambda route: fulfill_json(route, deep_research_state_payload()),
    )


def expect_trace_summary(
    page: Page,
    *,
    verdict: str = "pass",
    required_tools: list[str] | None = None,
    expected_interrupts: list[str] | None = None,
    required_research: bool | None = None,
) -> None:
    payload = page.evaluate("""async () => {
            const response = await fetch('/api/chat/runs/run_test/trace-summary');
            if (!response.ok) {
                throw new Error(`trace summary failed: ${response.status}`);
            }
            return await response.json();
        }""")
    assert payload["verdict"] == verdict
    for key, enabled in payload["failures"].items():
        assert not enabled, f"unexpected trace failure {key}: {payload}"
    for tool_name in required_tools or []:
        assert tool_name in payload["tool_names"], payload
    if expected_interrupts is not None:
        assert payload["interrupts"] == expected_interrupts, payload
    if required_research is not None:
        assert payload["research"]["required"] is required_research, payload


def setup_minimal_api_routes(page: Page) -> None:
    """Keep shell queries deterministic while preserving the real UI bundle."""

    page.route(
        "**/api/sessions",
        lambda route: (
            fulfill_json(
                route,
                {"sessions": [], "items": []},
            )
            if route.request.method == "GET"
            else route.fallback()
        ),
    )
    page.route(
        "**/api/providers",
        lambda route: fulfill_json(
            route,
            {
                "provider": "fake",
                "configured": True,
                "model": "concept-smoke",
                "base_url": None,
            },
        ),
    )
    page.route(
        "**/api/models",
        lambda route: fulfill_json(
            route,
            {
                "provider": "fake",
                "models": [
                    {
                        "id": "concept-smoke",
                        "name": "Concept Smoke",
                        "description": None,
                        "context_length": None,
                        "capability_profile": {"supports_tool_calls": True},
                    }
                ],
            },
        ),
    )
    page.route(
        "**/api/tools**",
        lambda route: fulfill_json(route, {"tools": [], "preset": "web"}),
    )


def open_new_chat(page: Page) -> None:
    page.goto(f"{BASE_URL}/sessions/new", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Chat")).to_be_visible(timeout=5000)


def send_chat_message(page: Page, text: str) -> None:
    page.get_by_role("textbox", name="Message the assistant…").fill(text)
    page.get_by_role("button", name="Send").click()


def run_clarification_regression(page: Page) -> None:
    """Plan survives tombstone; clarification is resumable and not approval UI."""

    resume_payloads: list[dict[str, Any]] = []
    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_completed",
                {
                    "planning_snapshot": {
                        "todos": [
                            {
                                "id": "s1",
                                "content": "Определить тему реферата",
                                "status": "in_progress",
                            },
                            {
                                "id": "s2",
                                "content": "Найти источники",
                                "status": "pending",
                            },
                        ],
                        "in_progress_id": "s1",
                        "completed": 0,
                        "total": 2,
                        "plan_title": "План реферата",
                    },
                    "tools": [
                        {
                            "tool_name": "todo_write",
                            "tool_call_id": "todo_1",
                            "status": "ok",
                        }
                    ],
                },
            ),
            sse_event(3, "assistant_message_tombstoned"),
            sse_event(
                4,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "ask_user_question",
                            "tool_call_id": "ask_1",
                            "status": "running",
                        }
                    ]
                },
            ),
            sse_event(5, "interrupt_requested", {"reason": "clarification_required"}),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            tool_names=["todo_write", "ask_user_question"],
            interrupts=["clarification_required"],
            planning_verdict="engaged",
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    page.route(
        "**/api/chat/runs/run_test/interrupt",
        lambda route: fulfill_json(
            route,
            {
                "run_id": "run_test",
                "interrupt_id": "interrupt_test",
                "reason": "clarification_required",
                "title": "User clarification required",
                "description": "Уточните тему, чтобы продолжить.",
                "proposed_action": {
                    "tool_name": "ask_user_question",
                    "args": {"prompt": "Какая тема?"},
                },
                "allowed_actions": ["clarify", "cancel"],
            },
        ),
    )

    def resume(route: Route) -> None:
        resume_payloads.append(route.request.post_data_json)
        fulfill_sse(route, sse_event(6, "run_completed"))

    page.route("**/api/chat/runs/run_test/resume", resume)

    open_new_chat(page)

    # 5-step user path: type task, send, read clarification, type answer, submit.
    send_chat_message(page, "составь план поиска информации по гитарам Fender")
    expect(page.get_by_text("Определить тему реферата")).to_be_visible(timeout=5000)
    expect(page.get_by_role("heading", name="Clarification required")).to_be_visible()
    expect(page.get_by_role("heading", name="Approval required")).not_to_be_visible()
    expect(page.get_by_text("ask_user_question")).not_to_be_visible(timeout=1000)

    send_button = page.get_by_role("button", name="Send clarification")
    expect(send_button).to_be_disabled()
    page.get_by_label("Clarify").fill("описание моделей")
    expect(send_button).to_be_enabled()
    send_button.click()

    page.wait_for_timeout(300)
    assert resume_payloads, "clarification resume endpoint was not called"
    assert resume_payloads[0]["action"] == "clarify"
    assert resume_payloads[0]["message"] == "описание моделей"
    expect(
        page.get_by_role("heading", name="Clarification required")
    ).not_to_be_visible()
    expect_trace_summary(
        page,
        required_tools=["todo_write", "ask_user_question"],
        expected_interrupts=["clarification_required"],
    )
    page.screenshot(
        path=str(ARTIFACT_DIR / "clarification-regression.png"),
        full_page=True,
    )


def run_plan_approval_regression(page: Page) -> None:
    """Plan approval interrupt stays distinct from clarification and resumes."""

    resume_payloads: list[dict[str, Any]] = []
    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(2, "token_delta", {"delta_text": "Сначала согласуем план."}),
            sse_event(3, "interrupt_requested", {"reason": "plan_approval_required"}),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            interrupts=["plan_approval_required"],
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    page.route(
        "**/api/chat/runs/run_test/interrupt",
        lambda route: fulfill_json(
            route,
            {
                "run_id": "run_test",
                "interrupt_id": "plan_interrupt",
                "reason": "plan_approval_required",
                "title": "Approve plan?",
                "description": "Review before execution.",
                "proposed_action": {
                    "args": {"reason": "ready"},
                    "plan_approval": {
                        "plan_id": "plan_1",
                        "content": "1. Найти источники\n2. Сверить факты\n3. Написать ответ",
                        "content_hash": "abc123",
                        "path": "docs/chat-demo-plan.md",
                    },
                },
                "allowed_actions": ["approve", "edit", "reject", "cancel"],
            },
        ),
    )

    def resume(route: Route) -> None:
        resume_payloads.append(route.request.post_data_json)
        fulfill_sse(route, sse_event(4, "run_completed"))

    page.route("**/api/chat/runs/run_test/resume", resume)

    open_new_chat(page)

    # 5-step user path: type task, send, inspect plan, edit plan, submit edit.
    send_chat_message(page, "please plan this change")
    expect(page.get_by_role("heading", name="Plan approval required")).to_be_visible(
        timeout=5000
    )
    expect(page.get_by_text("docs/chat-demo-plan.md")).to_be_visible()
    expect(page.locator("pre").filter(has_text="1. Найти источники")).to_be_visible()
    expect(
        page.get_by_role("heading", name="Clarification required")
    ).not_to_be_visible()

    page.locator("textarea").first.fill("1. Найти источники\n2. Проверить UI")
    page.get_by_role("button", name="Submit plan edit").click()

    page.wait_for_timeout(300)
    assert resume_payloads, "plan edit resume endpoint was not called"
    assert resume_payloads[0]["action"] == "edit"
    assert resume_payloads[0]["edited_tool_args"]["content"].endswith("Проверить UI")
    expect_trace_summary(page, expected_interrupts=["plan_approval_required"])
    page.screenshot(
        path=str(ARTIFACT_DIR / "plan-approval-regression.png"),
        full_page=True,
    )


def run_denied_tool_regression(page: Page) -> None:
    """A tombstoned assistant must not hide terminal policy-denied tools."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(2, "token_delta", {"delta_text": "Пробую выполнить запись."}),
            sse_event(
                3,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "tool_call_id": "file_write_1",
                            "status": "running",
                            "args": {"path": "docs/demo.txt"},
                        }
                    ]
                },
            ),
            sse_event(
                4,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "file_write",
                            "tool_call_id": "file_write_1",
                            "status": "denied",
                            "result_summary": (
                                "force planning requires an approved plan before writes"
                            ),
                            "risk": "high",
                        }
                    ]
                },
            ),
            sse_event(5, "assistant_message_tombstoned"),
            sse_event(
                6,
                "token_delta",
                {"delta_text": ("Запись заблокирована политикой. Сначала нужен план.")},
            ),
            sse_event(7, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(tool_names=["file_write"]),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type, send, see denied tool, expand details, see final answer.
    send_chat_message(page, "запиши результат в файл")
    expect(page.get_by_text("file_write")).to_be_visible(timeout=5000)
    expect(page.get_by_text("denied")).to_be_visible()
    expect(page.get_by_text("force planning requires an approved plan")).to_be_visible()
    expect(page.get_by_text("Запись заблокирована политикой")).to_be_visible()
    expect_trace_summary(page, required_tools=["file_write"])
    page.screenshot(
        path=str(ARTIFACT_DIR / "denied-tool-regression.png"),
        full_page=True,
    )


def run_web_search_final_answer(page: Page) -> None:
    """Web search/fetch activity remains visible and produces a final answer."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "web_1",
                            "status": "running",
                            "args": {"query": "Fender Stratocaster history"},
                        }
                    ]
                },
            ),
            sse_event(
                3,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "web_1",
                            "status": "ok",
                            "result_summary": (
                                "Found sources about Leo Fender and the 1954 Stratocaster."
                            ),
                            "duration_ms": 120,
                            "sources": [
                                {
                                    "id": "web_search:web_1:1",
                                    "url": "https://example.com/stratocaster",
                                    "canonical_url": "https://example.com/stratocaster",
                                    "source_type": "web_search",
                                    "title": "Stratocaster history",
                                    "excerpt": "Leo Fender introduced the model in 1954.",
                                    "rank": 1,
                                }
                            ],
                        }
                    ]
                },
            ),
            sse_event(
                4,
                "token_delta",
                {
                    "delta_text": (
                        "Fender Stratocaster стала важной моделью благодаря "
                        "эргономике, трем звукоснимателям и массовому влиянию "
                        "на рок- и поп-музыку."
                    )
                },
            ),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            tool_names=["web_search"],
            research_required=True,
            research_tools=["web_search"],
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type research request, send, see web tool, see source
    # summary, see final synthesized answer.
    send_chat_message(page, "найди в интернете краткую историю Fender Stratocaster")
    expect(page.get_by_text("web_search")).to_be_visible(timeout=5000)
    expect(page.get_by_text("done")).to_be_visible()
    expect(page.get_by_text("Query")).to_be_visible()
    expect(page.get_by_text("Fender Stratocaster history")).to_be_visible()
    expect(page.get_by_text('"query"')).not_to_be_visible(timeout=1000)
    expect(page.get_by_text("Found sources about Leo Fender")).to_be_visible()
    expect(page.get_by_text("Fender Stratocaster стала важной моделью")).to_be_visible()
    expect(page.get_by_label("Search candidates")).to_be_visible()
    expect(
        page.get_by_label("Search candidates").get_by_text(
            "Stratocaster history", exact=True
        )
    ).to_be_visible()
    expect(
        page.get_by_label("Search candidates").get_by_text("candidate", exact=True)
    ).to_be_visible()
    expect_trace_summary(
        page,
        required_tools=["web_search"],
        required_research=True,
    )
    page.screenshot(path=str(ARTIFACT_DIR / "web-search-final.png"), full_page=True)


def run_subagent_final_answer(page: Page) -> None:
    """Subagent fan-out is visible as runtime activity and ends with synthesis."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "tool_call_id": "agent_1",
                            "status": "running",
                            "args": {
                                "tasks": [
                                    "research Fender history",
                                    "verify model timeline",
                                ]
                            },
                        }
                    ]
                },
            ),
            sse_event(
                3,
                "subagent_group_started",
                {"group_id": "group_1", "tasks": 2},
            ),
            sse_event(
                4,
                "subagent_started",
                {
                    "group_id": "group_1",
                    "task_id": "researcher",
                    "child_run_id": "run_child_researcher",
                    "description": "Researcher",
                    "status": "running",
                },
            ),
            sse_event(
                5,
                "subagent_completed",
                {
                    "group_id": "group_1",
                    "task_id": "researcher",
                    "child_run_id": "run_child_researcher",
                    "description": "Researcher",
                    "status": "completed",
                    "summary": "history facts collected",
                },
            ),
            sse_event(
                6,
                "subagent_group_joined",
                {"group_id": "group_1", "join_state": "joined", "child_runs": 1},
            ),
            sse_event(
                7,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "agent_tool",
                            "tool_call_id": "agent_1",
                            "status": "ok",
                            "result_summary": (
                                "2 subagents completed: researcher and verifier."
                            ),
                        }
                    ]
                },
            ),
            sse_event(
                8,
                "token_delta",
                {
                    "delta_text": (
                        "Субагенты собрали историю и проверили временную линию. "
                        "Итог: Stratocaster появилась в 1954 году и стала одной "
                        "из ключевых электрогитар Fender."
                    )
                },
            ),
            sse_event(9, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(tool_names=["agent_tool"]),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type fan-out request, send, see delegated work, see
    # completed subagents, see final coordinator answer.
    send_chat_message(page, "поручи субагентам собрать и проверить факты о Fender")
    expect(
        page.get_by_role("button", name="Inspect delegated subagent work")
    ).to_be_visible(timeout=5000)
    expect(
        page.get_by_role("button", name="Inspect delegated subagent work").get_by_text(
            "joined", exact=True
        )
    ).to_be_visible()
    expect(page.get_by_text("Researcher", exact=True)).to_be_visible()
    expect(page.get_by_text("history facts collected")).to_be_visible()
    expect(page.get_by_text("2 subagents completed")).to_be_visible()
    expect(page.get_by_text("Субагенты собрали историю")).to_be_visible()
    expect_trace_summary(page, required_tools=["agent_tool"])
    page.screenshot(path=str(ARTIFACT_DIR / "subagent-final.png"), full_page=True)


def run_simple_direct_answer(page: Page) -> None:
    """Simple factual chat should not create plan, tools, or interrupts."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2, "token_delta", {"delta_text": "В слове strawberry три буквы r."}
            ),
            sse_event(3, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type simple question, send, see direct answer,
    # verify no plan panel, verify no tool/interrupt UI.
    send_chat_message(page, "сколько r в слове strawberry?")
    expect(page.get_by_text("В слове strawberry три буквы r.")).to_be_visible(
        timeout=5000
    )
    expect(page.get_by_text("PLAN")).not_to_be_visible(timeout=1000)
    expect(page.locator("button[aria-label*='tool call']")).to_have_count(0)
    expect(
        page.get_by_role("heading", name="Clarification required")
    ).not_to_be_visible()
    expect(
        page.get_by_role("heading", name="Plan approval required")
    ).not_to_be_visible()
    expect_trace_summary(page, required_tools=[])
    page.screenshot(path=str(ARTIFACT_DIR / "simple-direct-answer.png"), full_page=True)


def run_ask_question_denied_on_deliverable(page: Page) -> None:
    """Denied clarification should not become a user-facing interrupt."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "ask_user_question",
                            "tool_call_id": "ask_denied_1",
                            "status": "running",
                            "args": {
                                "prompt": "Какой формат нужен?",
                                "questions": [
                                    {
                                        "id": "format",
                                        "header": "Format",
                                        "question": "Какой формат нужен?",
                                        "choices": [
                                            {"id": "short", "label": "Short"},
                                            {"id": "full", "label": "Full"},
                                        ],
                                    }
                                ],
                            },
                        }
                    ]
                },
            ),
            sse_event(
                3,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "ask_user_question",
                            "tool_call_id": "ask_denied_1",
                            "status": "denied",
                            "result_summary": (
                                "deliverable request: clarification tool denied; "
                                "state reasonable assumptions and answer"
                            ),
                        }
                    ]
                },
            ),
            sse_event(
                4,
                "token_delta",
                {
                    "delta_text": (
                        "Принял разумное допущение: нужен краткий итог. "
                        "Вот готовый ответ без дополнительного уточнения."
                    )
                },
            ),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(tool_names=["ask_user_question"]),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type deliverable, send, confirm no clarification
    # card/raw control tool, see final text, keep composer usable.
    send_chat_message(page, "напиши короткий итог по найденным фактам, без вопросов")
    expect(
        page.get_by_text("Вот готовый ответ без дополнительного уточнения")
    ).to_be_visible(timeout=5000)
    expect(
        page.get_by_role("heading", name="Clarification required")
    ).not_to_be_visible()
    expect(page.get_by_text("ask_user_question")).not_to_be_visible(timeout=1000)
    expect(page.get_by_role("textbox", name="Message the assistant…")).to_be_enabled()
    expect_trace_summary(page, required_tools=["ask_user_question"])
    page.screenshot(
        path=str(ARTIFACT_DIR / "ask-question-denied-deliverable.png"),
        full_page=True,
    )


def run_deliverable_no_replan(page: Page) -> None:
    """A deliverable run may show progress, but must end with the deliverable."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_completed",
                {
                    "planning_snapshot": {
                        "todos": [
                            {
                                "id": "s1",
                                "content": "Собрать факты",
                                "status": "completed",
                            },
                            {
                                "id": "s2",
                                "content": "Написать черновик",
                                "status": "in_progress",
                            },
                        ],
                        "in_progress_id": "s2",
                        "completed": 1,
                        "total": 2,
                        "plan_title": "Черновик ответа",
                    },
                    "tools": [
                        {
                            "tool_name": "todo_write",
                            "tool_call_id": "todo_deliverable",
                            "status": "ok",
                        }
                    ],
                },
            ),
            sse_event(3, "assistant_message_tombstoned"),
            sse_event(
                4,
                "token_delta",
                {
                    "delta_text": (
                        "Готовый черновик: Fender стала важной компанией "
                        "благодаря массовому производству электрогитар, "
                        "моделям Telecaster и Stratocaster и влиянию на "
                        "популярную музыку."
                    )
                },
            ),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            tool_names=["todo_write"],
            planning_verdict="fabricated",
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: request deliverable, send, see plan panel preserved,
    # see final draft, confirm no clarification/approval modal took over.
    send_chat_message(page, "напиши черновик по Fender, не план")
    expect(page.get_by_text("Написать черновик")).to_be_visible(timeout=5000)
    expect(page.get_by_text("Готовый черновик: Fender")).to_be_visible()
    expect(
        page.get_by_role("heading", name="Clarification required")
    ).not_to_be_visible()
    expect(
        page.get_by_role("heading", name="Plan approval required")
    ).not_to_be_visible()
    expect_trace_summary(page, required_tools=["todo_write"])
    page.screenshot(
        path=str(ARTIFACT_DIR / "deliverable-no-replan.png"), full_page=True
    )


def run_plan_then_web_then_answer(page: Page) -> None:
    """A requested plan should progress into data tools and a final answer."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_completed",
                {
                    "planning_snapshot": {
                        "todos": [
                            {
                                "id": "s1",
                                "content": "Найти источники",
                                "status": "in_progress",
                            },
                            {
                                "id": "s2",
                                "content": "Сверить факты",
                                "status": "pending",
                            },
                            {
                                "id": "s3",
                                "content": "Дать ответ",
                                "status": "pending",
                            },
                        ],
                        "in_progress_id": "s1",
                        "completed": 0,
                        "total": 3,
                        "plan_title": "План исследования",
                    },
                    "tools": [
                        {
                            "tool_name": "todo_write",
                            "tool_call_id": "todo_research",
                            "status": "ok",
                        }
                    ],
                },
            ),
            sse_event(
                3,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "web_plan_1",
                            "status": "running",
                            "args": {"query": "Fender Stratocaster history"},
                        }
                    ]
                },
            ),
            sse_event(
                4,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "web_search",
                            "tool_call_id": "web_plan_1",
                            "status": "ok",
                            "result_summary": "Found 5 sources about Fender history.",
                            "sources": [
                                {
                                    "id": "web_search:web_plan_1:1",
                                    "url": "https://example.com/fender-history",
                                    "canonical_url": "https://example.com/fender-history",
                                    "source_type": "web_search",
                                    "title": "Fender history source",
                                    "excerpt": "A compact source on Fender history.",
                                    "rank": 1,
                                }
                            ],
                        }
                    ]
                },
            ),
            sse_event(
                5,
                "token_delta",
                {
                    "delta_text": (
                        "По найденным источникам: Fender закрепилась в истории "
                        "через массовые электрогитары, Telecaster, Stratocaster "
                        "и стандартизацию звучания популярных жанров."
                    )
                },
            ),
            sse_event(6, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            tool_names=["todo_write", "web_search"],
            research_required=True,
            research_tools=["web_search"],
            planning_verdict="engaged",
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 6-step visible path: ask plan+research, send, see plan panel, see web
    # execution, see source summary, see synthesized answer.
    send_chat_message(page, "составь план и найди факты о Fender")
    expect(page.get_by_text("Найти источники")).to_be_visible(timeout=5000)
    expect(page.get_by_text("web_search")).to_be_visible()
    expect(page.get_by_text("Query")).to_be_visible()
    expect(page.get_by_text("Fender Stratocaster history")).to_be_visible()
    expect(page.get_by_text('"query"')).not_to_be_visible(timeout=1000)
    expect(page.get_by_text("Found 5 sources about Fender history")).to_be_visible()
    expect(page.get_by_text("По найденным источникам")).to_be_visible()
    expect(page.get_by_label("Search candidates")).to_be_visible()
    expect(
        page.get_by_label("Search candidates").get_by_text(
            "Fender history source", exact=True
        )
    ).to_be_visible()
    expect_trace_summary(
        page,
        required_tools=["todo_write", "web_search"],
        required_research=True,
    )
    page.screenshot(path=str(ARTIFACT_DIR / "plan-web-answer.png"), full_page=True)


def run_markdown_math(page: Page) -> None:
    """Math in assistant prose renders as KaTeX without tool UI."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "token_delta",
                {"delta_text": ("Вероятность: $$P(X > 3) = e^{-2.35 \\cdot 3}$$")},
            ),
            sse_event(3, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "посчитай вероятность x > 3")
    expect(page.locator(".katex")).to_be_visible(timeout=5000)
    expect(page.get_by_text("$$")).not_to_be_visible(timeout=1000)
    expect(page.locator("button[aria-label*='tool call']")).to_have_count(0)
    expect_trace_summary(page, required_tools=[])
    page.screenshot(path=str(ARTIFACT_DIR / "markdown-math.png"), full_page=True)


def run_deep_research_cockpit(page: Page) -> None:
    """Deep Research cockpit survives stream completion and session reload."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "source_ledger_updated",
                {
                    "search_candidates": [
                        {
                            "url": "https://example.com/candidate",
                            "canonical_url": "https://example.com/candidate",
                            "source_type": "web_search",
                            "title": "Candidate source",
                            "domain": "example.com",
                        }
                    ],
                    "verified_reads": [],
                    "blocked_reads": [
                        {
                            "url": "https://blocked.example/source",
                            "canonical_url": "https://blocked.example/source",
                            "source_type": "web_fetch",
                            "title": "Blocked source",
                            "domain": "blocked.example",
                            "excerpt": "fetch_denied",
                        }
                    ],
                    "failed_reads": [],
                },
            ),
            sse_event(
                3,
                "deep_research_artifact_updated",
                {
                    "deep_research_artifacts": {
                        "report_path": "research/report.md",
                        "report_size_bytes": 2048,
                        "captured_long_answers": 1,
                    }
                },
            ),
            sse_event(
                4,
                "token_delta",
                {"delta_text": "Full report saved to `research/report.md`."},
            ),
            sse_event(5, "run_completed"),
        ]
    )
    trace = trace_summary_payload(
        tool_names=["todo_write", "agent_tool", "web_fetch", "file_write"],
        research_required=True,
        research_tools=["web_fetch"],
    )
    trace["research_efficiency"] = {
        "contract_ok": True,
        "quality_ok": False,
        "quality_status": "candidate_only",
    }
    route_trace_summary(page, trace)
    route_deep_research_state(page)
    page.route(
        "**/api/workspace/session_dr/artifacts",
        lambda route: fulfill_json(
            route,
            {
                "ok": True,
                "sessionId": "session_dr",
                "artifacts": [
                    {
                        "path": "research/report.md",
                        "kind": "research_report",
                        "sizeBytes": 2048,
                        "modifiedAt": "2026-06-04T00:00:00Z",
                    }
                ],
            },
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "run deep research medium")
    cockpit = page.get_by_label("Deep Research cockpit")
    expect(cockpit).to_be_visible(timeout=5000)
    expect(cockpit.get_by_text("quality: candidate_only")).to_be_visible()
    expect(page.get_by_text("research/report.md · captured_inline")).to_be_visible()
    expect(page.get_by_text("candidate: example.com")).to_be_visible()
    expect(page.get_by_text("children 1/1")).to_be_visible()
    expect(
        cockpit.get_by_text("research/report.md · captured_inline · preview/download")
    ).to_be_visible()

    page.route(
        "**/api/sessions/session_dr",
        lambda route: fulfill_json(
            route,
            {
                "session_id": "session_dr",
                "thread_id": "thread_dr",
                "title": "Deep Research",
                "run_ids": ["run_test"],
                "transcript": [
                    {"role": "user", "content": "run deep research medium"},
                    {
                        "role": "assistant",
                        "content": "Full report saved to `research/report.md`.",
                    },
                ],
                "metadata_by_run": {
                    "run_test": {
                        "research_mode": "deep",
                        "research_profile": "medium",
                        "profile_source": "user_selected",
                    }
                },
                "created_at": "2026-06-04T00:00:00Z",
                "updated_at": "2026-06-04T00:00:00Z",
            },
        ),
    )
    page.goto(f"{BASE_URL}/sessions/session_dr", wait_until="networkidle")
    cockpit = page.get_by_label("Deep Research cockpit")
    expect(cockpit).to_be_visible(timeout=5000)
    expect(cockpit.get_by_text("quality: candidate_only")).to_be_visible()
    page.screenshot(
        path=str(ARTIFACT_DIR / "deep-research-cockpit.png"), full_page=True
    )


def run_markdown_code_python(page: Page) -> None:
    """Python fenced code renders with header, copy button, and containment."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "token_delta",
                {
                    "delta_text": (
                        "```python\n" "def add(a, b):\n" "    return a + b\n" "```\n"
                    )
                },
            ),
            sse_event(3, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "покажи пример Python функции")
    expect(page.get_by_text("Python", exact=True)).to_be_visible(timeout=5000)
    expect(page.get_by_text("Copy", exact=True)).to_be_visible()
    expect(page.locator("pre.hljs-block")).to_be_visible()
    expect_trace_summary(page, required_tools=[])
    page.screenshot(path=str(ARTIFACT_DIR / "markdown-code-python.png"), full_page=True)


def run_python_tool_answer(page: Page) -> None:
    """Exact arithmetic shows Python execution and a Markdown final answer."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "python",
                            "tool_call_id": "py_1",
                            "status": "running",
                            "args": {"code": "print(17 * 23 + 11)"},
                        }
                    ]
                },
            ),
            sse_event(
                3,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "python",
                            "tool_call_id": "py_1",
                            "status": "ok",
                            "result_summary": "402",
                            "duration_ms": 32,
                        }
                    ]
                },
            ),
            sse_event(
                4,
                "token_delta",
                {"delta_text": "Точный результат: **402**."},
            ),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload(tool_names=["python"]))
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "посчитай точно 17 * 23 + 11")
    expect(page.get_by_text("Python calculation")).to_be_visible(timeout=5000)
    expect(page.locator("strong").filter(has_text="402")).to_be_visible()
    expect_trace_summary(page, required_tools=["python"])
    page.screenshot(path=str(ARTIFACT_DIR / "python-tool-answer.png"), full_page=True)


def run_web_fetch_sources(page: Page) -> None:
    """Fetched pages appear as source cards under the final answer."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "tool_call_started",
                {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "tool_call_id": "fetch_1",
                            "status": "running",
                            "args": {"url": "https://example.com/fender"},
                        }
                    ]
                },
            ),
            sse_event(
                3,
                "tool_call_completed",
                {
                    "tools": [
                        {
                            "tool_name": "web_fetch",
                            "tool_call_id": "fetch_1",
                            "status": "ok",
                            "result_summary": "Fetched page about Fender.",
                            "sources": [
                                {
                                    "id": "web_fetch:fetch_1:1",
                                    "url": "https://example.com/fender",
                                    "canonical_url": "https://example.com/fender",
                                    "source_type": "web_fetch",
                                    "title": "Fetched Fender page",
                                    "excerpt": "A fetched page excerpt.",
                                    "rank": 1,
                                }
                            ],
                        }
                    ]
                },
            ),
            sse_event(4, "token_delta", {"delta_text": "Краткий ответ по источнику."}),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            tool_names=["web_fetch"],
            research_required=True,
            research_tools=["web_fetch"],
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "прочитай страницу и дай вывод")
    expect(page.get_by_text("Web fetch")).to_be_visible(timeout=5000)
    expect(page.get_by_label("Sources")).to_be_visible()
    expect(
        page.get_by_label("Sources").get_by_text("Fetched Fender page")
    ).to_be_visible()
    expect(
        page.get_by_label("Sources").get_by_text("fetched", exact=True)
    ).to_be_visible()
    expect_trace_summary(page, required_tools=["web_fetch"], required_research=True)
    page.screenshot(path=str(ARTIFACT_DIR / "web-fetch-sources.png"), full_page=True)


def run_assistant_link_sources(page: Page) -> None:
    """Assistant Markdown links create source cards even without web tools."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "token_delta",
                {
                    "delta_text": (
                        "См. [документацию](https://example.com/docs) для деталей."
                    )
                },
            ),
            sse_event(3, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "ответь со ссылкой")
    expect(page.get_by_label("Sources")).to_be_visible(timeout=5000)
    expect(page.get_by_label("Sources").get_by_text("документацию")).to_be_visible()
    expect(
        page.get_by_label("Sources").get_by_text("linked", exact=True)
    ).to_be_visible()
    expect_trace_summary(page, required_tools=[])
    page.screenshot(
        path=str(ARTIFACT_DIR / "assistant-link-sources.png"), full_page=True
    )


def run_streaming_partial_fence(page: Page) -> None:
    """Unfinished streaming fences stay plain and do not create broken code UI."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "token_delta",
                {"delta_text": "Intro\n\n```python\nprint('still streaming')"},
            ),
        ]
    )

    route_trace_summary(
        page,
        trace_summary_payload(
            verdict="pass",
            terminal_event=None,
            failures={"missing_terminal_event": False},
        ),
    )
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "стримь незакрытый код")
    expect(page.get_by_text("Intro")).to_be_visible(timeout=5000)
    expect(page.get_by_text("```python")).to_be_visible()
    expect(page.get_by_text("Writing")).to_be_visible()
    expect(page.get_by_text("Python", exact=True)).not_to_be_visible(timeout=1000)
    expect(page.get_by_text("Copy", exact=True)).not_to_be_visible(timeout=1000)
    page.screenshot(
        path=str(ARTIFACT_DIR / "streaming-partial-fence.png"), full_page=True
    )


def run_xss_markdown(page: Page) -> None:
    """Malicious Markdown remains inert in the rendered chat."""

    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "token_delta",
                {
                    "delta_text": (
                        "<script>window.__chat_demo_xss = true</script>\n\n"
                        "[bad](javascript:alert(1)) safe text"
                    )
                },
            ),
            sse_event(3, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "проверь xss markdown")
    expect(page.get_by_text("bad")).to_be_visible(timeout=5000)
    expect(page.get_by_role("link", name="bad")).not_to_be_visible(timeout=1000)
    assert page.evaluate("() => window.__chat_demo_xss === true") is False
    expect_trace_summary(page, required_tools=[])
    page.screenshot(path=str(ARTIFACT_DIR / "xss-markdown.png"), full_page=True)


def run_compaction_start_success(page: Page) -> None:
    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "memory_compaction_started",
                {
                    "compaction_id": "cmp_success",
                    "mode": "partial",
                    "reason": "token_pressure",
                },
            ),
            sse_event(
                3, "token_delta", {"delta_text": "Continuing after memory work."}
            ),
            sse_event(
                4,
                "memory_compacted",
                {
                    "compaction_id": "cmp_success",
                    "mode": "partial",
                    "outcome": "success",
                    "summarized_message_count": 7,
                },
            ),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "trigger compaction")
    expect(page.get_by_text("Conversation memory compacted")).to_be_visible(
        timeout=5000
    )
    expect(
        page.get_by_text("Older context was summarized across 7 messages.")
    ).to_be_visible()
    expect(page.get_by_text("Continuing after memory work.")).to_be_visible()
    page.screenshot(
        path=str(ARTIFACT_DIR / "compaction-start-success.png"), full_page=True
    )


def run_compaction_failed_warning(page: Page) -> None:
    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "memory_compaction_started",
                {
                    "compaction_id": "cmp_failed",
                    "mode": "llm_full",
                    "reason": "token_pressure",
                },
            ),
            sse_event(
                3,
                "memory_compacted",
                {
                    "compaction_id": "cmp_failed",
                    "mode": "llm_full",
                    "outcome": "failure",
                    "failure_kind": "llm_timeout",
                },
            ),
            sse_event(4, "token_delta", {"delta_text": "I can still continue."}),
            sse_event(5, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "trigger failed compaction")
    expect(page.get_by_text("Memory compaction needs attention")).to_be_visible(
        timeout=5000
    )
    expect(
        page.get_by_text("The run continued, but compaction failed: llm_timeout.")
    ).to_be_visible()
    expect(page.get_by_text("I can still continue.")).to_be_visible()
    page.screenshot(
        path=str(ARTIFACT_DIR / "compaction-failed-warning.png"), full_page=True
    )


def run_compaction_skipped_hidden(page: Page) -> None:
    start_body = "".join(
        [
            sse_event(1, "run_started"),
            sse_event(
                2,
                "memory_compacted",
                {
                    "compaction_id": "cmp_skipped",
                    "mode": "none",
                    "outcome": "skipped",
                },
            ),
            sse_event(3, "token_delta", {"delta_text": "No compaction was needed."}),
            sse_event(4, "run_completed"),
        ]
    )

    route_trace_summary(page, trace_summary_payload())
    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    send_chat_message(page, "skip compaction")
    expect(page.get_by_text("No compaction was needed.")).to_be_visible(timeout=5000)
    expect(page.get_by_text("Conversation memory compacted")).not_to_be_visible()
    expect(page.get_by_text("Compacting conversation memory")).not_to_be_visible()
    page.screenshot(
        path=str(ARTIFACT_DIR / "compaction-skipped-hidden.png"), full_page=True
    )


SCENARIOS = {
    "assistant-link-sources": run_assistant_link_sources,
    "ask-question-denied": run_ask_question_denied_on_deliverable,
    "clarification": run_clarification_regression,
    "compaction-failed-warning": run_compaction_failed_warning,
    "compaction-skipped-hidden": run_compaction_skipped_hidden,
    "compaction-start-success": run_compaction_start_success,
    "deep-research-cockpit": run_deep_research_cockpit,
    "denied-tool": run_denied_tool_regression,
    "deliverable-no-replan": run_deliverable_no_replan,
    "markdown-code-python": run_markdown_code_python,
    "markdown-math": run_markdown_math,
    "plan-approval": run_plan_approval_regression,
    "plan-web-answer": run_plan_then_web_then_answer,
    "python-tool-answer": run_python_tool_answer,
    "simple-direct": run_simple_direct_answer,
    "streaming-partial-fence": run_streaming_partial_fence,
    "subagent-final": run_subagent_final_answer,
    "web-fetch-sources": run_web_fetch_sources,
    "web-search-final": run_web_search_final_answer,
    "xss-markdown": run_xss_markdown,
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="Scenario to run. Defaults to all scenarios.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    scenario_names = args.scenario or sorted(SCENARIOS)

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headed is False)
        try:
            for name in scenario_names:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                setup_minimal_api_routes(page)
                SCENARIOS[name](page)
                page.close()
                print(f"ok: {name}")
        finally:
            browser.close()


if __name__ == "__main__":
    main()
