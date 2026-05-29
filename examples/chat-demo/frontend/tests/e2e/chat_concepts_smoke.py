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
            {"models": ["concept-smoke"], "selected": "concept-smoke"},
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

    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type, send, see denied tool, expand details, see final answer.
    send_chat_message(page, "запиши результат в файл")
    expect(page.get_by_text("file_write")).to_be_visible(timeout=5000)
    expect(page.get_by_text("denied")).to_be_visible()
    expect(page.get_by_text("force planning requires an approved plan")).to_be_visible()
    expect(page.get_by_text("Запись заблокирована политикой")).to_be_visible()
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

    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type research request, send, see web tool, see source
    # summary, see final synthesized answer.
    send_chat_message(page, "найди в интернете краткую историю Fender Stratocaster")
    expect(page.get_by_text("web_search")).to_be_visible(timeout=5000)
    expect(page.get_by_text("done")).to_be_visible()
    expect(page.get_by_text("Found sources about Leo Fender")).to_be_visible()
    expect(page.get_by_text("Fender Stratocaster стала важной моделью")).to_be_visible()
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
                4,
                "token_delta",
                {
                    "delta_text": (
                        "Субагенты собрали историю и проверили временную линию. "
                        "Итог: Stratocaster появилась в 1954 году и стала одной "
                        "из ключевых электрогитар Fender."
                    )
                },
            ),
            sse_event(5, "run_completed"),
        ]
    )

    page.route("**/api/chat/messages", lambda route: fulfill_sse(route, start_body))
    open_new_chat(page)

    # 5-step visible path: type fan-out request, send, see agent tool, see
    # completed subagents, see final coordinator answer.
    send_chat_message(page, "поручи субагентам собрать и проверить факты о Fender")
    expect(page.get_by_text("agent_tool")).to_be_visible(timeout=5000)
    expect(page.get_by_text("2 subagents completed")).to_be_visible()
    expect(page.get_by_text("Субагенты собрали историю")).to_be_visible()
    page.screenshot(path=str(ARTIFACT_DIR / "subagent-final.png"), full_page=True)


SCENARIOS = {
    "clarification": run_clarification_regression,
    "denied-tool": run_denied_tool_regression,
    "plan-approval": run_plan_approval_regression,
    "subagent-final": run_subagent_final_answer,
    "web-search-final": run_web_search_final_answer,
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
