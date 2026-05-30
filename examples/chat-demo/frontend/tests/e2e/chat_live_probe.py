"""Live Playwright probes for chat-demo model behavior.

Run against an already running chat-demo stack:

    CHAT_DEMO_URL=http://localhost:5174 \
      .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py \
      --scenario simple-direct

Unlike ``chat_concepts_smoke.py``, this script does not mock SSE. It sends real
messages, captures the run id from the SSE response headers, fetches the
backend trace summary, and stores artifacts under ``/tmp`` by default.
"""

from __future__ import annotations

import argparse
import json
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.environ.get("CHAT_DEMO_URL", "http://localhost:5174")
ARTIFACT_DIR = Path(
    os.environ.get("CHAT_DEMO_LIVE_ARTIFACT_DIR", "/tmp/chat-demo-live")
)


@dataclass(frozen=True, slots=True)
class LiveScenario:
    """One live chat scenario and its trace-level acceptance criteria."""

    name: str
    prompt: str
    required_tools: tuple[str, ...] = ()
    forbidden_failures: tuple[str, ...] = (
        "stuck_on_interrupt",
        "missing_terminal_event",
        "run_failed_or_cancelled",
        "missing_required_research_evidence",
        "progress_only_final",
        "text_form_tool_call",
        "fabricated_planning",
    )
    requires_research: bool | None = None


SCENARIOS: dict[str, LiveScenario] = {
    "simple-direct": LiveScenario(
        name="simple-direct",
        prompt="сколько r в слове strawberry?",
    ),
    "research-report": LiveScenario(
        name="research-report",
        prompt=(
            "найди в интернете один источник про историю Fender Stratocaster "
            "и дай краткий итог со ссылкой"
        ),
        required_tools=("web_search",),
        requires_research=True,
    ),
    "plan-web-answer": LiveScenario(
        name="plan-web-answer",
        prompt=(
            "составь план поиска информации в интернете по гитарам Fender "
            "и затем дай краткий итог"
        ),
        required_tools=("web_search",),
        requires_research=True,
    ),
}


def open_new_chat(page: Page) -> None:
    page.goto(f"{BASE_URL}/sessions/new", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Chat")).to_be_visible(timeout=5000)


def send_message_and_capture_run_id(page: Page, text: str) -> str:
    textbox = page.get_by_role("textbox", name="Message the assistant…")
    textbox.fill(text)
    with page.expect_response(
        lambda response: response.url.endswith("/api/chat/messages")
        and response.request.method == "POST",
        timeout=15000,
    ) as response_info:
        page.get_by_role("button", name="Send").click()
    run_id = response_info.value.headers.get("x-run-id")
    if not run_id:
        raise AssertionError("chat/messages response did not include x-run-id")
    return run_id


def fetch_trace_summary(page: Page, run_id: str) -> dict[str, Any]:
    return page.evaluate(
        """async (runId) => {
            const response = await fetch(`/api/chat/runs/${runId}/trace-summary`);
            if (!response.ok) {
                throw new Error(`trace summary failed: ${response.status}`);
            }
            return await response.json();
        }""",
        run_id,
    )


def wait_until_run_idle(
    page: Page,
    *,
    run_id: str,
    timeout_ms: int,
) -> dict[str, Any]:
    """Wait for backend terminal state; UI controls can lag provider/tool work."""
    deadline = time.monotonic() + timeout_ms / 1000
    latest: dict[str, Any] | None = None
    while time.monotonic() < deadline:
        try:
            latest = fetch_trace_summary(page, run_id)
        except Exception:
            latest = None
        if latest and latest.get("terminal_event") is not None:
            expect(
                page.get_by_role("textbox", name="Message the assistant…")
            ).to_be_visible(timeout=15000)
            return latest
        page.wait_for_timeout(1000)
    raise AssertionError(f"run did not finish before timeout: {latest}")


def assert_trace_acceptance(
    scenario: LiveScenario,
    summary: dict[str, Any],
) -> list[str]:
    failures: list[str] = []
    for name in scenario.forbidden_failures:
        if summary.get("failures", {}).get(name) is True:
            failures.append(f"failure flag is set: {name}")
    tools = set(summary.get("tool_names") or [])
    for tool_name in scenario.required_tools:
        if tool_name not in tools:
            failures.append(f"required tool missing: {tool_name}")
    if scenario.requires_research is not None:
        required = summary.get("research", {}).get("required")
        if required is not scenario.requires_research:
            failures.append(f"research.required={required!r}")
    if summary.get("verdict") != "pass":
        failures.append(f"summary verdict is {summary.get('verdict')!r}")
    return failures


def run_scenario(page: Page, scenario: LiveScenario) -> dict[str, Any]:
    open_new_chat(page)
    run_id = send_message_and_capture_run_id(page, scenario.prompt)
    summary = wait_until_run_idle(page, run_id=run_id, timeout_ms=180000)
    failures = assert_trace_acceptance(scenario, summary)
    artifact_base = ARTIFACT_DIR / scenario.name
    artifact_base.mkdir(parents=True, exist_ok=True)
    (artifact_base / "trace-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_base / "scenario.json").write_text(
        json.dumps(
            {"name": scenario.name, "prompt": scenario.prompt, "failures": failures},
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    page.screenshot(path=str(artifact_base / "screenshot.png"), full_page=True)
    if failures:
        raise AssertionError("; ".join(failures))
    return summary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--scenario",
        action="append",
        choices=sorted(SCENARIOS),
        help="Scenario to run. Defaults to simple-direct.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    scenario_names = args.scenario or ["simple-direct"]
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headed is False)
        try:
            for name in scenario_names:
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                summary = run_scenario(page, SCENARIOS[name])
                print(
                    "ok: "
                    f"{name} run_id={summary['run_id']} tools={summary['tool_names']}"
                )
                page.close()
        finally:
            browser.close()


if __name__ == "__main__":
    main()
