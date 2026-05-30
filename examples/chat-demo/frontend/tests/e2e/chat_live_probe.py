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
import shutil
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
    forbidden_tools: tuple[str, ...] = ()
    tool_preset: str | None = None
    requires_subagent: bool = False
    requires_parent_synthesis: bool = False
    max_planning_tool_calls: int | None = None
    steering_message: str | None = None
    requires_steering: bool = False
    forbidden_failures: tuple[str, ...] = (
        "stuck_on_interrupt",
        "missing_terminal_event",
        "run_failed_or_cancelled",
        "missing_required_research_evidence",
        "progress_only_final",
        "text_form_tool_call",
        "fabricated_planning",
        "repeated_approval_planning",
        "extra_ask_user_question",
        "missed_explicit_delegation",
        "unnecessary_delegation",
        "subagent_no_final",
        "child_result_not_used",
        "child_prompt_not_bounded",
        "missed_python",
        "python_no_final",
        "python_policy_loop",
        "unnecessary_python",
        "python_result_ignored",
    )
    requires_research: bool | None = None


SCENARIOS: dict[str, LiveScenario] = {
    "simple-direct": LiveScenario(
        name="simple-direct",
        prompt="привет, ответь одной короткой фразой",
        forbidden_tools=("python", "agent_tool"),
        requires_research=False,
    ),
    "python-count-letters": LiveScenario(
        name="python-count-letters",
        prompt="Сколько букв r в strawberry? Проверь точно.",
        required_tools=("python",),
        forbidden_tools=("agent_tool", "web_search", "web_fetch"),
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "python-arithmetic": LiveScenario(
        name="python-arithmetic",
        prompt="Посчитай точно: 17 * 23 + 19% от 350, округли до двух знаков.",
        required_tools=("python",),
        forbidden_tools=("agent_tool", "web_search", "web_fetch"),
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "python-statistics": LiveScenario(
        name="python-statistics",
        prompt="Вычисли среднее и медиану для чисел 4, 9, 15, 16, 23, 42.",
        required_tools=("python",),
        forbidden_tools=("agent_tool", "web_search", "web_fetch"),
        max_planning_tool_calls=0,
        requires_research=False,
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
    "web-search-final": LiveScenario(
        name="web-search-final",
        prompt=(
            "найди в интернете один свежий источник про Python 3.13 "
            "и дай короткий итог со ссылкой"
        ),
        required_tools=("web_search",),
        requires_research=True,
    ),
    "plan-only": LiveScenario(
        name="plan-only",
        prompt="составь только план поиска информации по истории Fender, без реферата",
        required_tools=("todo_write",),
        forbidden_failures=(
            "stuck_on_interrupt",
            "missing_terminal_event",
            "run_failed_or_cancelled",
            "missing_required_research_evidence",
            "progress_only_final",
            "text_form_tool_call",
            "repeated_approval_planning",
            "extra_ask_user_question",
        ),
        requires_research=False,
    ),
    "deliverable-no-replan": LiveScenario(
        name="deliverable-no-replan",
        prompt=(
            "напиши короткий реферат на 3 абзаца об истории Fender, "
            "не план и не список шагов"
        ),
        forbidden_failures=(
            "stuck_on_interrupt",
            "missing_terminal_event",
            "run_failed_or_cancelled",
            "missing_required_research_evidence",
            "progress_only_final",
            "text_form_tool_call",
            "fabricated_planning",
            "repeated_approval_planning",
            "extra_ask_user_question",
        ),
        requires_research=False,
    ),
    "clarification-only-when-blocked": LiveScenario(
        name="clarification-only-when-blocked",
        prompt=(
            "найди в интернете краткую информацию о Fender Jazzmaster и дай итог; "
            "если нужно выбрать аспект, выбери сам"
        ),
        required_tools=("web_search",),
        requires_research=True,
    ),
    "subagent-synthesis": LiveScenario(
        name="subagent-synthesis",
        prompt=(
            "Поручи субагенту кратко собрать по памяти 3 факта о Fender Jazzmaster, "
            "а затем сам дай итог в 2 предложения. Без поиска в интернете."
        ),
        required_tools=("agent_tool",),
        tool_preset="web",
        requires_subagent=True,
        requires_parent_synthesis=True,
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "subagent-explicit-delegation": LiveScenario(
        name="subagent-explicit-delegation",
        prompt=(
            "Поручи субагенту проверить по памяти, чем Fender Jazzmaster "
            "отличается от Stratocaster, а затем сам дай итог в 3 пункта. "
            "Без поиска в интернете."
        ),
        required_tools=("agent_tool",),
        tool_preset="web",
        requires_subagent=True,
        requires_parent_synthesis=True,
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "subagent-autonomous-delegation": LiveScenario(
        name="subagent-autonomous-delegation",
        prompt=(
            "Сравни два варианта структуры короткого ответа о Fender Jazzmaster: "
            "исторический обзор и сравнение с Stratocaster. Выбери лучший вариант, "
            "предварительно проверь аргументы отдельным исполнителем. Без поиска в интернете."
        ),
        required_tools=("agent_tool",),
        tool_preset="web",
        requires_subagent=True,
        requires_parent_synthesis=True,
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "subagent-no-delegation-simple": LiveScenario(
        name="subagent-no-delegation-simple",
        prompt="сколько r в слове strawberry?",
        required_tools=("python",),
        forbidden_tools=("agent_tool",),
        tool_preset="web",
        requires_research=False,
    ),
    "steering-mid-run": LiveScenario(
        name="steering-mid-run",
        prompt=(
            "Найди в интернете один источник про историю Fender Jazzmaster "
            "и дай краткий итог."
        ),
        required_tools=("web_search",),
        steering_message=(
            "Уточнение во время работы: в финальном ответе добавь короткую "
            "фразу 'учтено steering'."
        ),
        requires_steering=True,
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


def queue_steering_message(page: Page, run_id: str, message: str) -> dict[str, Any]:
    return page.evaluate(
        """async ({runId, message}) => {
            const response = await fetch(`/api/chat/runs/${runId}/control`, {
                method: "POST",
                headers: {"content-type": "application/json"},
                body: JSON.stringify({
                    kind: "enqueue_user_message",
                    priority: "next",
                    payload: {message}
                })
            });
            if (!response.ok) {
                throw new Error(`control request failed: ${response.status}`);
            }
            return await response.json();
        }""",
        {"runId": run_id, "message": message},
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
    if latest is None:
        latest = {
            "run_id": run_id,
            "verdict": "fail",
            "terminal_event": None,
            "failures": {"missing_terminal_event": True},
            "notes": ["Live probe timed out before trace summary was available."],
        }
    latest["probe_timeout"] = True
    return latest


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
    for tool_name in scenario.forbidden_tools:
        if tool_name in tools:
            failures.append(f"forbidden tool used: {tool_name}")
    if scenario.requires_subagent:
        subagents = summary.get("subagents") or {}
        if not isinstance(subagents, dict):
            failures.append("subagent summary missing")
        else:
            if subagents.get("runs_completed", 0) < 1:
                failures.append("subagent run did not complete")
            if subagents.get("groups_joined", 0) < 1:
                failures.append("subagent group did not join")
    if scenario.requires_parent_synthesis:
        subagents = summary.get("subagents") or {}
        if not isinstance(subagents, dict):
            failures.append("subagent summary missing")
        elif subagents.get("parent_synthesized_final") is not True:
            failures.append("parent did not synthesize subagent final answer")
    if scenario.max_planning_tool_calls is not None:
        planning = summary.get("planning") or {}
        planning_tool_calls = (
            planning.get("planning_tool_calls") if isinstance(planning, dict) else None
        )
        if (
            not isinstance(planning_tool_calls, int)
            or planning_tool_calls > scenario.max_planning_tool_calls
        ):
            failures.append(
                "planning tool calls exceeded limit: "
                f"{planning_tool_calls!r} > {scenario.max_planning_tool_calls}"
            )
    if scenario.requires_steering:
        controls = summary.get("controls") or {}
        if not isinstance(controls, dict):
            failures.append("control summary missing")
        else:
            if controls.get("queued", 0) < 1:
                failures.append("steering command was not queued")
            if controls.get("dequeued", 0) < 1:
                failures.append("steering command was not dequeued")
            if controls.get("applied", 0) < 1:
                failures.append("steering command was not applied")
    if summary.get("probe_timeout") is True:
        failures.append("probe timed out before terminal event")
    if scenario.requires_research is not None:
        required = summary.get("research", {}).get("required")
        if required is not scenario.requires_research:
            failures.append(f"research.required={required!r}")
    if summary.get("verdict") != "pass":
        failures.append(f"summary verdict is {summary.get('verdict')!r}")
    return failures


def transcript_excerpt(page: Page, *, max_chars: int = 6000) -> str:
    """Return a bounded visible transcript excerpt for failed live probes."""
    text = page.locator("main").inner_text(timeout=5000)
    compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[-max_chars:]


def write_scenario_artifacts(
    *,
    page: Page,
    scenario: LiveScenario,
    summary: dict[str, Any],
    failures: list[str],
) -> Path:
    """Persist enough context to debug a live scenario without reopening the UI."""
    artifact_base = ARTIFACT_DIR / scenario.name
    artifact_base.mkdir(parents=True, exist_ok=True)
    (artifact_base / "trace-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_base / "scenario.json").write_text(
        json.dumps(
            {
                "name": scenario.name,
                "prompt": scenario.prompt,
                "failures": failures,
                "run_id": summary.get("run_id"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (artifact_base / "transcript-excerpt.txt").write_text(
        transcript_excerpt(page),
        encoding="utf-8",
    )
    page.screenshot(path=str(artifact_base / "screenshot.png"), full_page=True)
    if failures:
        latest_failed = ARTIFACT_DIR / "latest-failed"
        if latest_failed.exists():
            shutil.rmtree(latest_failed)
        shutil.copytree(artifact_base, latest_failed)
    return artifact_base


def run_scenario(page: Page, scenario: LiveScenario) -> dict[str, Any]:
    open_new_chat(page)
    page.route(
        "**/api/chat/messages",
        lambda route: route.continue_(
            post_data=json.dumps(
                {
                    **json.loads(route.request.post_data or "{}"),
                    "scenario_id": scenario.name,
                    **(
                        {"tool_preset": scenario.tool_preset}
                        if scenario.tool_preset
                        else {}
                    ),
                },
                ensure_ascii=False,
            )
        ),
    )
    run_id = send_message_and_capture_run_id(page, scenario.prompt)
    if scenario.steering_message:
        queue_steering_message(page, run_id, scenario.steering_message)
    summary = wait_until_run_idle(page, run_id=run_id, timeout_ms=180000)
    failures = assert_trace_acceptance(scenario, summary)
    write_scenario_artifacts(
        page=page,
        scenario=scenario,
        summary=summary,
        failures=failures,
    )
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
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all live scenarios.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    scenario_names = (
        sorted(SCENARIOS) if args.all else args.scenario or ["simple-direct"]
    )
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
