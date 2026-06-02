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
import re
import shutil
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import quote

from playwright.sync_api import Page, expect, sync_playwright

BASE_URL = os.environ.get("CHAT_DEMO_URL", "http://localhost:5174")
ARTIFACT_DIR = Path(
    os.environ.get("CHAT_DEMO_LIVE_ARTIFACT_DIR", "/tmp/chat-demo-live")
)
MODEL_OVERRIDE = os.environ.get("CHAT_DEMO_LIVE_MODEL")
REQUIRE_OBSERVABILITY = (
    os.environ.get("CHAT_DEMO_LIVE_REQUIRE_OBSERVABILITY", "1").strip().lower()
    not in {"0", "false", "no"}
)


@dataclass(frozen=True, slots=True)
class LiveScenario:
    """One live chat scenario and its trace-level acceptance criteria."""

    name: str
    prompt: str
    required_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    tool_preset: str | None = None
    research_mode: str | None = None
    research_profile: str | None = None
    profile_source: str | None = None
    requires_subagent: bool = False
    requires_parent_synthesis: bool = False
    required_prompt_fragments: tuple[str, ...] = ()
    forbidden_prompt_fragments: tuple[str, ...] = ()
    max_planning_tool_calls: int | None = None
    steering_message: str | None = None
    requires_steering: bool = False
    requires_compaction: bool = False
    min_research_fetch_count: int | None = None
    min_research_fetch_attempt_count: int | None = None
    min_research_domain_count: int | None = None
    max_research_search_count_without_min_domains: int | None = None
    max_research_fetch_count_without_min_domains: int | None = None
    research_depth: str | None = None
    required_artifact_path: str | None = None
    required_artifact_preview: str | None = None
    require_artifact_panel: bool = False
    require_research_efficiency: bool = False
    max_phase_violations_before_stop: int | None = 4
    max_tokens_before_report_projection: int | None = 60000
    max_subagent_runs_before_stop: int | None = None
    timeout_ms: int = 180000
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
        "search_only_research_report",
        "insufficient_research_source_diversity",
        "final_missing_source_links",
        "plan_todos_incomplete_on_final",
    )
    requires_research: bool | None = None


SCENARIOS: dict[str, LiveScenario] = {
    "simple-direct": LiveScenario(
        name="simple-direct",
        prompt="привет, ответь одной короткой фразой",
        forbidden_tools=("python", "agent_tool"),
        requires_research=False,
    ),
    "prompt-surface-no-web": LiveScenario(
        name="prompt-surface-no-web",
        prompt="привет, ответь одной короткой фразой",
        forbidden_tools=("web_search", "web_fetch", "agent_tool"),
        forbidden_prompt_fragments=(
            "react_chat_tool_policy_web_search.txt",
            "react_chat_tool_policy_web_fetch.txt",
        ),
        tool_preset="off",
        requires_research=False,
    ),
    "prompt-surface-fetch-only": LiveScenario(
        name="prompt-surface-fetch-only",
        prompt="привет, ответь одной короткой фразой",
        forbidden_tools=("web_search", "web_fetch", "agent_tool"),
        required_prompt_fragments=("react_chat_tool_policy_web_fetch.txt",),
        forbidden_prompt_fragments=("react_chat_tool_policy_web_search.txt",),
        tool_preset="web_fetch",
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
    "python-combinatorics": LiveScenario(
        name="python-combinatorics",
        prompt=(
            "Посчитай точно: сколько существует способов выбрать 5 карт "
            "из колоды 52 карты? Используй расчет."
        ),
        required_tools=("python",),
        forbidden_tools=("agent_tool", "web_search", "web_fetch"),
        max_planning_tool_calls=0,
        requires_research=False,
    ),
    "web-plus-python": LiveScenario(
        name="web-plus-python",
        prompt=(
            "Найди в интернете текущую численность населения США и Канады, "
            "а затем через расчет скажи, во сколько раз население США больше. "
            "Дай короткий ответ со ссылкой."
        ),
        required_tools=("web_search", "python"),
        forbidden_tools=("agent_tool",),
        requires_research=True,
    ),
    "model-preflight-web-search": LiveScenario(
        name="model-preflight-web-search",
        prompt=(
            "Найди в интернете 2 источника про fork-join queueing models и "
            "дай одну короткую фразу со ссылкой. Не открывай страницы."
        ),
        required_tools=("web_search",),
        forbidden_tools=("web_fetch", "agent_tool"),
        tool_preset="web_search",
        timeout_ms=180000,
        requires_research=True,
    ),
    "model-preflight-web-fetch-direct": LiveScenario(
        name="model-preflight-web-fetch-direct",
        prompt=(
            "Открой URL https://en.wikipedia.org/wiki/Fork–join_queue "
            "через web_fetch и дай одно предложение о том, что это такое, "
            "со ссылкой на источник."
        ),
        required_tools=("web_fetch",),
        forbidden_tools=("web_search", "agent_tool"),
        tool_preset="web_fetch",
        min_research_fetch_count=1,
        timeout_ms=180000,
        requires_research=True,
    ),
    "model-preflight-search-fetch": LiveScenario(
        name="model-preflight-search-fetch",
        prompt=(
            "Найди в интернете источник про fork-join queueing models, открой "
            "один найденный URL через web_fetch и дай 2 коротких вывода со ссылкой."
        ),
        required_tools=("web_search", "web_fetch"),
        forbidden_tools=("agent_tool",),
        tool_preset="web",
        min_research_fetch_count=1,
        timeout_ms=240000,
        requires_research=True,
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
    "research-report-requires-fetch": LiveScenario(
        name="research-report-requires-fetch",
        prompt=(
            "составь todo лист и иди по нему. Мне нужно поискать информацию "
            "в интернете о fork-join моделях массового обслуживания и их "
            "применении для расчета компьютерных сетей"
        ),
        required_tools=("web_search", "web_fetch"),
        required_prompt_fragments=(
            "react_chat_tool_policy_research_discipline.txt",
            "react_chat_tool_policy_web_search.txt",
            "react_chat_tool_policy_web_fetch.txt",
        ),
        min_research_fetch_count=2,
        min_research_domain_count=2,
        max_research_search_count_without_min_domains=10,
        max_research_fetch_count_without_min_domains=10,
        timeout_ms=600000,
        requires_research=True,
    ),
    "deep-research-artifact": LiveScenario(
        name="deep-research-artifact",
        prompt=(
            "Сделай deep research отчет по fork-join очередям и применению "
            "для расчета компьютерных сетей."
        ),
        required_tools=("todo_write", "web_search", "web_fetch", "file_write"),
        forbidden_tools=("bash", "python"),
        tool_preset="deep_research",
        research_mode="deep",
        research_profile="medium",
        profile_source="scenario_forced",
        research_depth="deep_parallel_research",
        required_prompt_fragments=(
            "react_chat_tool_policy_research_discipline.txt",
            "react_chat_tool_policy_web_search.txt",
            "react_chat_tool_policy_web_fetch.txt",
        ),
        required_artifact_path="research/report.md",
        required_artifact_preview="Fork-join queueing models",
        require_artifact_panel=True,
        require_research_efficiency=True,
        timeout_ms=240000,
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
            "search_only_research_report",
            "deep_research_no_report_artifact",
            "deep_research_no_source_ledger_artifact",
            "deep_research_full_report_rewrite",
            "deep_research_stale_report_edit",
            "deep_research_repeated_report_read",
            "deep_research_final_missing_report_reference",
            "deep_research_missing_initial_todo",
            "deep_research_unexpected_agent_tool",
            "deep_research_skill_denied",
            "deep_research_low_verified_coverage",
            "deep_research_preliminary_final",
            "deep_research_repeated_search_args",
            "deep_research_search_without_fetch_progress",
            "deep_research_tool_entropy_high",
            "deep_research_phase_violation",
            "deep_research_long_final_after_report",
        ),
        requires_research=True,
    ),
    "deep-research-targeted-patch": LiveScenario(
        name="deep-research-targeted-patch",
        prompt=(
            "Сделай deep research отчет по fork-join очередям, затем проверь "
            "черновик и внеси точечное уточнение без полной перезаписи."
        ),
        required_tools=(
            "todo_write",
            "web_search",
            "web_fetch",
            "file_write",
            "artifact_preview",
            "file_patch",
        ),
        forbidden_tools=("bash", "python"),
        tool_preset="deep_research",
        research_mode="deep",
        research_profile="medium",
        profile_source="scenario_forced",
        research_depth="deep_parallel_research",
        required_prompt_fragments=(
            "react_chat_tool_policy_research_discipline.txt",
            "react_chat_tool_policy_web_search.txt",
            "react_chat_tool_policy_web_fetch.txt",
        ),
        required_artifact_path="research/report.md",
        required_artifact_preview="Updated after preview.",
        require_artifact_panel=True,
        require_research_efficiency=True,
        timeout_ms=240000,
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
            "search_only_research_report",
            "deep_research_no_report_artifact",
            "deep_research_no_source_ledger_artifact",
            "deep_research_full_report_rewrite",
            "deep_research_stale_report_edit",
            "deep_research_repeated_report_read",
            "deep_research_final_missing_report_reference",
            "deep_research_missing_initial_todo",
            "deep_research_unexpected_agent_tool",
            "deep_research_skill_denied",
            "deep_research_low_verified_coverage",
            "deep_research_preliminary_final",
            "deep_research_repeated_search_args",
            "deep_research_search_without_fetch_progress",
            "deep_research_tool_entropy_high",
            "deep_research_phase_violation",
            "deep_research_long_final_after_report",
        ),
        requires_research=True,
    ),
    "deep-research-blocked-fetch": LiveScenario(
        name="deep-research-blocked-fetch",
        prompt=(
            "Сделай deep research отчет по fork-join очередям, но явно "
            "зафиксируй caveat, если источники заблокируют чтение."
        ),
        required_tools=("todo_write", "web_search", "web_fetch", "file_write"),
        forbidden_tools=("bash", "python"),
        tool_preset="deep_research",
        research_mode="deep",
        research_profile="medium",
        profile_source="scenario_forced",
        research_depth="deep_parallel_research",
        required_prompt_fragments=(
            "react_chat_tool_policy_research_discipline.txt",
            "react_chat_tool_policy_web_search.txt",
            "react_chat_tool_policy_web_fetch.txt",
        ),
        min_research_fetch_attempt_count=2,
        required_artifact_path="research/report.md",
        required_artifact_preview="blocked automated reads",
        require_artifact_panel=True,
        require_research_efficiency=True,
        timeout_ms=240000,
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
            "search_only_research_report",
            "deep_research_no_report_artifact",
            "deep_research_no_source_ledger_artifact",
            "deep_research_full_report_rewrite",
            "deep_research_stale_report_edit",
            "deep_research_repeated_report_read",
            "deep_research_final_missing_report_reference",
            "deep_research_missing_initial_todo",
            "deep_research_unexpected_agent_tool",
            "deep_research_skill_denied",
            "deep_research_low_verified_coverage",
            "deep_research_preliminary_final",
            "deep_research_repeated_search_args",
            "deep_research_search_without_fetch_progress",
            "deep_research_tool_entropy_high",
            "deep_research_phase_violation",
            "deep_research_long_final_after_report",
        ),
        requires_research=True,
    ),
    "research-compare-frameworks": LiveScenario(
        name="research-compare-frameworks",
        prompt=(
            "сравни FastAPI и Django для нового API-сервиса, найди источники "
            "в интернете и дай короткий вывод со ссылками"
        ),
        required_tools=("web_search", "web_fetch"),
        min_research_fetch_count=2,
        min_research_domain_count=2,
        timeout_ms=300000,
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
    "compaction-notice": LiveScenario(
        name="compaction-notice",
        prompt="trigger synthetic compaction notice",
        requires_compaction=True,
        forbidden_tools=("web_search", "web_fetch", "agent_tool", "python"),
        requires_research=False,
    ),
}

MODEL_PREFLIGHT_SCENARIOS: tuple[str, ...] = (
    "simple-direct",
    "model-preflight-web-search",
    "model-preflight-web-fetch-direct",
    "model-preflight-search-fetch",
    "research-report-requires-fetch",
)


def open_new_chat(page: Page) -> None:
    page.goto(f"{BASE_URL}/sessions/new", wait_until="networkidle")
    expect(page.get_by_role("heading", name="Chat")).to_be_visible(timeout=5000)


@dataclass(frozen=True, slots=True)
class ChatRunIds:
    """Identifiers returned by the chat message endpoint."""

    run_id: str
    session_id: str


def send_message_and_capture_run_ids(page: Page, text: str) -> ChatRunIds:
    existing_run_ids = fetch_known_run_ids(page)
    textbox = page.get_by_role("textbox", name="Message the assistant…")
    textbox.fill(text)
    with page.expect_response(
        lambda response: response.url.endswith("/api/chat/messages")
        and response.request.method == "POST",
        timeout=15000,
    ) as response_info:
        page.get_by_role("button", name="Send").click()
    response = response_info.value
    if not response.ok:
        try:
            body = response.text()[:1000]
        except Exception as exc:
            body = f"<response body unavailable: {exc}>"
        raise AssertionError(
            f"chat/messages failed: status={response.status} body={body}"
        )
    run_id = response.headers.get("x-run-id")
    session_id = response.headers.get("x-session-id")
    if not run_id or not session_id:
        fallback_ids = wait_for_run_ids_from_sessions(
            page,
            existing_run_ids=existing_run_ids,
        )
        run_id = run_id or fallback_ids.run_id
        session_id = session_id or fallback_ids.session_id
    if not run_id:
        raise AssertionError("chat/messages response did not include or expose x-run-id")
    if not session_id:
        raise AssertionError("chat/messages response did not include or expose x-session-id")
    return ChatRunIds(run_id=run_id, session_id=session_id)


def fetch_known_run_ids(page: Page) -> set[str]:
    run_ids: set[str] = set()
    try:
        sessions_response = page.context.request.get(
            f"{BASE_URL}/api/sessions",
            timeout=10000,
        )
        if not sessions_response.ok:
            return run_ids
        sessions_payload = sessions_response.json()
        for item in sessions_payload.get("sessions", []):
            if not isinstance(item, dict):
                continue
            session_id = item.get("session_id") or item.get("sessionId")
            if not session_id:
                continue
            detail_response = page.context.request.get(
                f"{BASE_URL}/api/sessions/{quote(str(session_id), safe='')}",
                timeout=10000,
            )
            if not detail_response.ok:
                continue
            detail = detail_response.json()
            for run_id in detail.get("run_ids", []):
                if isinstance(run_id, str):
                    run_ids.add(run_id)
    except Exception:
        return run_ids
    return run_ids


def wait_for_run_ids_from_sessions(
    page: Page,
    *,
    existing_run_ids: set[str],
    timeout_ms: int = 15000,
) -> ChatRunIds:
    deadline = time.monotonic() + timeout_ms / 1000
    while time.monotonic() < deadline:
        try:
            sessions_response = page.context.request.get(
                f"{BASE_URL}/api/sessions",
                timeout=10000,
            )
            if sessions_response.ok:
                sessions_payload = sessions_response.json()
                for item in sessions_payload.get("sessions", []):
                    if not isinstance(item, dict):
                        continue
                    session_id = item.get("session_id") or item.get("sessionId")
                    if not session_id:
                        continue
                    detail_response = page.context.request.get(
                        f"{BASE_URL}/api/sessions/{quote(str(session_id), safe='')}",
                        timeout=10000,
                    )
                    if not detail_response.ok:
                        continue
                    detail = detail_response.json()
                    for run_id in reversed(detail.get("run_ids", [])):
                        if isinstance(run_id, str) and run_id not in existing_run_ids:
                            return ChatRunIds(run_id=run_id, session_id=str(session_id))
        except Exception:
            pass
        time.sleep(0.25)
    return ChatRunIds(run_id="", session_id="")


def fetch_trace_summary(page: Page, run_id: str) -> dict[str, Any]:
    response = page.context.request.get(
        f"{BASE_URL}/api/chat/runs/{quote(run_id, safe='')}/trace-summary",
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"trace summary failed: {response.status}")
    return response.json()


def fetch_workspace_artifacts(page: Page, session_id: str) -> dict[str, Any]:
    response = page.context.request.get(
        f"{BASE_URL}/api/workspace/{quote(session_id, safe='')}/artifacts",
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"workspace artifacts failed: {response.status}")
    return response.json()


def fetch_workspace_artifact_preview(
    page: Page,
    *,
    session_id: str,
    path: str,
) -> dict[str, Any]:
    encoded_path = "/".join(quote(part, safe="") for part in path.split("/"))
    response = page.context.request.get(
        f"{BASE_URL}/api/workspace/{quote(session_id, safe='')}/artifacts/{encoded_path}",
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"workspace preview failed: {response.status}")
    return response.json()


def fetch_health_status(page: Page) -> dict[str, Any]:
    response = page.context.request.get(
        f"{BASE_URL}/api/health",
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"health failed: {response.status}")
    return response.json()


def assert_observability_preflight(page: Page) -> dict[str, Any]:
    health = fetch_health_status(page)
    if not REQUIRE_OBSERVABILITY:
        return health
    failures: list[str] = []
    if health.get("store_kind") == "memory":
        failures.append("runtime store is memory; use sqlite/jsonl for live traces")
    tracing = health.get("tracing") if isinstance(health.get("tracing"), dict) else {}
    if tracing.get("enabled") is not True:
        failures.append(f"Phoenix tracing is not enabled: {tracing}")
    provider = health.get("provider") if isinstance(health.get("provider"), dict) else {}
    if provider.get("configured") is not True:
        failures.append(f"provider is not configured: {provider}")
    if failures:
        raise AssertionError("observability preflight failed: " + "; ".join(failures))
    return health


def queue_steering_message(page: Page, run_id: str, message: str) -> dict[str, Any]:
    response = page.context.request.post(
        f"{BASE_URL}/api/chat/runs/{quote(run_id, safe='')}/control",
        data={
            "kind": "enqueue_user_message",
            "priority": "next",
            "payload": {"message": message},
        },
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"control request failed: {response.status}")
    return response.json()


def cancel_run(page: Page, run_id: str) -> dict[str, Any]:
    response = page.context.request.post(
        f"{BASE_URL}/api/chat/runs/{quote(run_id, safe='')}/cancel",
        timeout=10000,
    )
    if not response.ok:
        raise RuntimeError(f"cancel request failed: {response.status}")
    return response.json()


def research_budget_stop_reason(
    scenario: LiveScenario,
    summary: dict[str, Any],
) -> str | None:
    failures = summary.get("failures") if isinstance(summary.get("failures"), dict) else {}
    if failures.get("unknown_tool_call") is True:
        unknown_tools = summary.get("unknown_tools")
        return f"unknown tool call detected: {unknown_tools or 'see trace-summary'}"
    efficiency = (
        summary.get("research_efficiency")
        if isinstance(summary.get("research_efficiency"), dict)
        else {}
    )
    if scenario.require_research_efficiency and isinstance(efficiency, dict):
        subagents = (
            summary.get("subagents")
            if isinstance(summary.get("subagents"), dict)
            else {}
        )
        max_subagents = _max_subagent_runs_before_stop(scenario)
        started_children = subagents.get("runs_started")
        if (
            isinstance(started_children, int)
            and max_subagents is not None
            and started_children > max_subagents
        ):
            return (
                "deep research subagent fan-out budget exhausted: "
                f"{started_children} > {max_subagents}"
            )
        unexpected_after_child = subagents.get(
            "unexpected_tool_after_child_synthesis_pending"
        )
        allowed_after_child_before_report = {"file_write", "todo_write", "web_fetch"}
        report_seen = (
            efficiency.get("report_trace_update_seen") is True
            or efficiency.get("report_write_seen") is True
        )
        if (
            subagents.get("child_synthesis_pending") is True
            and not report_seen
            and isinstance(unexpected_after_child, str)
            and unexpected_after_child
            and unexpected_after_child not in allowed_after_child_before_report
        ):
            return (
                "deep research parent synthesis contract violated after child "
                f"join: {unexpected_after_child}"
            )
        phase_violations = efficiency.get("phase_violation_count")
        max_phase_violations = scenario.max_phase_violations_before_stop
        if (
            isinstance(phase_violations, int)
            and max_phase_violations is not None
            and phase_violations > max_phase_violations
        ):
            return (
                "deep research phase violation budget exhausted: "
                f"{phase_violations} > {max_phase_violations}"
            )
        max_tokens = scenario.max_tokens_before_report_projection
        total_tokens = efficiency.get("total_tokens")
        report_seen = (
            efficiency.get("report_trace_update_seen") is True
            or efficiency.get("report_write_seen") is True
        )
        if (
            isinstance(total_tokens, int)
            and max_tokens is not None
            and total_tokens > max_tokens
            and not report_seen
        ):
            return (
                "deep research token budget exhausted before report projection: "
                f"{total_tokens} > {max_tokens}"
            )
    if scenario.min_research_domain_count is None:
        return None
    research = summary.get("research") or {}
    if not isinstance(research, dict):
        return None
    domains = research.get("unique_domains")
    domain_count = len(domains) if isinstance(domains, list) else 0
    if domain_count >= scenario.min_research_domain_count:
        return None

    search_count = research.get("search_count")
    max_search = scenario.max_research_search_count_without_min_domains
    if isinstance(search_count, int) and max_search is not None:
        if search_count >= max_search:
            return (
                "research search budget exhausted before source diversity: "
                f"{search_count} searches, {domain_count} domains"
            )

    fetch_count = research.get("fetch_count")
    max_fetch = scenario.max_research_fetch_count_without_min_domains
    if isinstance(fetch_count, int) and max_fetch is not None:
        if fetch_count >= max_fetch:
            return (
                "research fetch budget exhausted before source diversity: "
                f"{fetch_count} fetches, {domain_count} domains"
            )
    return None


def _max_subagent_runs_before_stop(scenario: LiveScenario) -> int | None:
    if scenario.max_subagent_runs_before_stop is not None:
        return scenario.max_subagent_runs_before_stop
    if scenario.research_profile == "light":
        return 0
    if scenario.research_profile == "medium":
        return 1
    if scenario.research_profile == "hard":
        return 4
    return None


def wait_until_run_idle(
    page: Page,
    *,
    scenario: LiveScenario,
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
        if latest:
            budget_stop_reason = research_budget_stop_reason(scenario, latest)
            if budget_stop_reason is not None:
                latest["probe_budget_stop"] = True
                latest["probe_budget_stop_reason"] = budget_stop_reason
                try:
                    latest["probe_cancel_response"] = cancel_run(page, run_id)
                except Exception as exc:
                    latest["probe_cancel_error"] = str(exc)
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
    prompt_surface = summary.get("prompt_surface") or {}
    prompt_fragments = set(
        prompt_surface.get("prompt_fragments", [])
        if isinstance(prompt_surface, dict)
        else []
    )
    for fragment in scenario.required_prompt_fragments:
        if fragment not in prompt_fragments:
            failures.append(f"required prompt fragment missing: {fragment}")
    for fragment in scenario.forbidden_prompt_fragments:
        if fragment in prompt_fragments:
            failures.append(f"forbidden prompt fragment present: {fragment}")
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
    if scenario.requires_compaction:
        compaction = summary.get("compaction") or {}
        if not isinstance(compaction, dict):
            failures.append("compaction summary missing")
        elif compaction.get("successful", 0) < 1:
            failures.append("compaction did not complete successfully")
    if summary.get("probe_timeout") is True:
        failures.append("probe timed out before terminal event")
    if summary.get("probe_budget_stop") is True:
        failures.append(str(summary.get("probe_budget_stop_reason")))
    if scenario.requires_research is not None:
        required = summary.get("research", {}).get("required")
        if required is not scenario.requires_research:
            failures.append(f"research.required={required!r}")
    if scenario.require_research_efficiency:
        efficiency = summary.get("research_efficiency")
        if not isinstance(efficiency, dict):
            failures.append("research_efficiency summary missing")
        else:
            if efficiency.get("deep_research_artifact_expected") is not True:
                failures.append("deep research artifact was not expected")
            if efficiency.get("missing_report_artifact") is True:
                failures.append("research report artifact is missing")
            if efficiency.get("missing_source_ledger_artifact") is True:
                failures.append("research source ledger artifact is missing")
            if efficiency.get("full_report_rewrite") is True:
                failures.append(
                    "research report was fully rewritten after initial draft"
                )
            if efficiency.get("stale_report_edit") is True:
                failures.append("research report was edited without a fresh read")
            if efficiency.get("repeated_report_read") is True:
                failures.append("research report was read repeatedly without changes")
            if efficiency.get("final_missing_report_reference") is True:
                failures.append("final answer did not reference research/report.md")
            if efficiency.get("long_final_after_report") is True:
                failures.append("long final answer was emitted after report artifact")
            if efficiency.get("repeated_search_args") is True:
                failures.append("deep research repeated identical search queries")
            if efficiency.get("search_without_fetch_progress") is True:
                failures.append(
                    "deep research expanded search without fetch/ledger progress"
                )
            if efficiency.get("tool_entropy_high") is True:
                failures.append("deep research exceeded search/tool entropy budget")
            if efficiency.get("phase_violation") is True:
                failures.append("deep research violated the phase tool contract")
            if efficiency.get("missing_initial_todo") is True:
                failures.append(
                    "deep research did not create an initial todo before discovery"
                )
    artifacts = summary.get("artifacts") or {}
    if scenario.required_artifact_path is not None:
        paths = artifacts.get("paths") if isinstance(artifacts, dict) else None
        if not isinstance(paths, list) or scenario.required_artifact_path not in paths:
            failures.append(
                f"required artifact missing from trace: {scenario.required_artifact_path}"
            )
    research = summary.get("research") or {}
    if scenario.min_research_fetch_count is not None:
        fetch_count = (
            research.get("fetch_count") if isinstance(research, dict) else None
        )
        if (
            not isinstance(fetch_count, int)
            or fetch_count < scenario.min_research_fetch_count
        ):
            failures.append(
                "research.fetch_count below minimum: "
                f"{fetch_count!r} < {scenario.min_research_fetch_count}"
            )
    if scenario.min_research_fetch_attempt_count is not None:
        fetch_attempt_count = (
            research.get("fetch_attempt_count") if isinstance(research, dict) else None
        )
        if (
            not isinstance(fetch_attempt_count, int)
            or fetch_attempt_count < scenario.min_research_fetch_attempt_count
        ):
            failures.append(
                "research.fetch_attempt_count below minimum: "
                f"{fetch_attempt_count!r} < "
                f"{scenario.min_research_fetch_attempt_count}"
            )
    if scenario.min_research_domain_count is not None:
        domains = research.get("unique_domains") if isinstance(research, dict) else None
        domain_count = len(domains) if isinstance(domains, list) else None
        if (
            not isinstance(domain_count, int)
            or domain_count < scenario.min_research_domain_count
        ):
            failures.append(
                "research.unique_domains below minimum: "
                f"{domain_count!r} < {scenario.min_research_domain_count}"
            )
    if summary.get("verdict") != "pass":
        failures.append(f"summary verdict is {summary.get('verdict')!r}")
    return failures


def reconcile_workspace_artifact_failures(
    *,
    scenario: LiveScenario,
    summary: dict[str, Any],
    failures: list[str],
    workspace_artifacts: dict[str, Any] | None,
) -> list[str]:
    """Replace false report-missing failures with explicit projection mismatch."""
    required_path = scenario.required_artifact_path
    if required_path is None or not isinstance(workspace_artifacts, dict):
        return failures
    workspace_paths = {
        str(item.get("path"))
        for item in workspace_artifacts.get("artifacts", [])
        if isinstance(item, dict) and isinstance(item.get("path"), str)
    }
    if required_path not in workspace_paths:
        return failures

    missing_report_markers = {
        "failure flag is set: deep_research_no_report_artifact",
        "research report artifact is missing",
        f"required artifact missing from trace: {required_path}",
    }
    trace_had_report = (
        required_path
        in (
            (summary.get("artifacts") or {}).get("paths")
            if isinstance(summary.get("artifacts"), dict)
            else []
        )
    )
    reconciled = [
        failure for failure in failures if failure not in missing_report_markers
    ]
    if len(reconciled) != len(failures) or not trace_had_report:
        reconciled.append(
            "report artifact projection mismatch: "
            f"workspace has {required_path}, but trace/scorecard did not "
            "recognize a report artifact update"
        )
    return reconciled


def assert_artifact_panel(
    page: Page,
    *,
    scenario: LiveScenario,
) -> None:
    if not scenario.require_artifact_panel:
        return
    if scenario.required_artifact_path is None:
        raise AssertionError("artifact panel assertion requires artifact path")
    trigger = page.get_by_role("button", name=re.compile(r"Artifacts"))
    expect(trigger).to_be_visible(timeout=10000)
    trigger.click()
    refresh = page.get_by_role("button", name="Refresh artifacts")
    expect(refresh).to_be_visible(timeout=10000)
    refresh.click()
    expect(page.get_by_text(scenario.required_artifact_path).first).to_be_visible(
        timeout=10000
    )
    if scenario.required_artifact_preview:
        expect(
            page.get_by_text(scenario.required_artifact_preview).first
        ).to_be_visible(timeout=10000)


def transcript_excerpt(page: Page, *, max_chars: int = 6000) -> str:
    """Return a bounded visible transcript excerpt for failed live probes."""
    text = page.locator("main").inner_text(timeout=5000)
    compact = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    if len(compact) <= max_chars:
        return compact
    return compact[-max_chars:]


def render_scenario_scorecard(
    *,
    scenario: LiveScenario,
    summary: dict[str, Any],
    failures: list[str],
    workspace_artifacts: dict[str, Any] | None = None,
    workspace_preview: dict[str, Any] | None = None,
    health_status: dict[str, Any] | None = None,
) -> str:
    """Render a compact markdown scorecard for one live probe run."""
    llm = summary.get("llm") if isinstance(summary.get("llm"), dict) else {}
    usage = llm.get("usage") if isinstance(llm, dict) else None
    if not isinstance(usage, dict):
        usage = {}
    research = (
        summary.get("research") if isinstance(summary.get("research"), dict) else {}
    )
    artifacts = (
        summary.get("artifacts") if isinstance(summary.get("artifacts"), dict) else {}
    )
    efficiency = (
        summary.get("research_efficiency")
        if isinstance(summary.get("research_efficiency"), dict)
        else {}
    )
    workspace_paths = []
    if isinstance(workspace_artifacts, dict):
        for item in workspace_artifacts.get("artifacts", []):
            if isinstance(item, dict) and isinstance(item.get("path"), str):
                workspace_paths.append(item["path"])
    report_path = scenario.required_artifact_path or "research/report.md"
    trace_paths = artifacts.get("paths") if isinstance(artifacts, dict) else []
    if not isinstance(trace_paths, list):
        trace_paths = []
    report_workspace_exists = report_path in workspace_paths
    report_trace_path_seen = report_path in trace_paths
    preview_size = None
    preview_truncated = None
    if isinstance(workspace_preview, dict):
        content = workspace_preview.get("content")
        if isinstance(content, str):
            preview_size = len(content)
        preview_truncated = workspace_preview.get("truncated")
    tracing = (
        health_status.get("tracing")
        if isinstance(health_status, dict)
        and isinstance(health_status.get("tracing"), dict)
        else {}
    )
    rows = [
        f"# Live Probe Scorecard: {scenario.name}",
        "",
        f"- run_id: `{summary.get('run_id')}`",
        f"- verdict: `{summary.get('verdict')}` terminal=`{summary.get('terminal_event')}`",
        f"- failures: `{', '.join(failures) if failures else '-'}`",
        f"- tool_chain: `{summary.get('tool_chain') or '-'}`",
        (
            "- tokens: "
            f"input=`{usage.get('input_tokens', 0)}`, "
            f"output=`{usage.get('output_tokens', 0)}`, "
            f"total=`{usage.get('total_tokens', 0)}`, "
            f"after_report=`{efficiency.get('output_tokens_after_first_report_update', 0)}`"
        ),
        (
            "- research: "
            f"search=`{research.get('search_count', 0)}`, "
            f"fetch=`{research.get('fetch_count', 0)}`, "
            f"attempts=`{research.get('fetch_attempt_count', research.get('fetch_count', 0))}`, "
            f"domains=`{len(research.get('unique_domains') or [])}`, "
            f"readiness=`{summary.get('final_readiness')}`"
        ),
        (
            "- artifacts: "
            f"trace=`{', '.join(trace_paths) or '-'}`, "
            f"workspace=`{', '.join(workspace_paths) if workspace_paths else '-'}`, "
            f"report_updates=`{efficiency.get('report_update_count', 0)}`, "
            f"full_writes=`{efficiency.get('report_full_write_count', 0)}`, "
            f"stale_edits=`{efficiency.get('report_targeted_edit_without_fresh_read_count', 0)}`, "
            f"repeat_reads=`{efficiency.get('repeated_unchanged_report_read_count', 0)}`, "
            f"source_records=`{efficiency.get('source_ledger_record_count', 0)}`"
        ),
        (
            "- report_projection: "
            f"workspace_exists=`{report_workspace_exists}`, "
            f"trace_path_seen=`{report_trace_path_seen}`, "
            f"trace_update_seen=`{efficiency.get('report_trace_update_seen', False)}`, "
            f"write_seen=`{efficiency.get('report_write_seen', False)}`"
        ),
        (
            "- artifact_preview: "
            f"chars=`{preview_size if preview_size is not None else 0}`, "
            f"truncated=`{preview_truncated if preview_truncated is not None else False}`"
        ),
        (
            "- deep_research: "
            f"expected=`{efficiency.get('deep_research_artifact_expected', False)}`, "
            f"phase=`{efficiency.get('deep_research_phase') or '-'}`, "
            f"phase_violations=`{efficiency.get('phase_violation_count', 0)}`, "
            f"status=`{efficiency.get('report_status') or '-'}`, "
            f"verified=`{efficiency.get('verified_read_count', 0)}`, "
            f"search_budget=`{efficiency.get('search_budget_status') or '-'}`, "
            f"repeat_queries=`{efficiency.get('repeated_search_query_count', 0)}`, "
            f"first_tool=`{efficiency.get('first_tool') or '-'}`, "
            f"final_refs_report=`{efficiency.get('final_references_report_artifact', False)}`, "
            f"long_final_after_report=`{efficiency.get('long_final_after_report', False)}`"
        ),
        (
            "- phoenix: "
            f"enabled=`{tracing.get('enabled', False)}`, "
            f"configured=`{tracing.get('configured', False)}`, "
            f"error=`{tracing.get('error') or '-'}`"
        ),
        "",
    ]
    return "\n".join(rows)


def write_scenario_artifacts(
    *,
    page: Page,
    scenario: LiveScenario,
    summary: dict[str, Any],
    failures: list[str],
    workspace_artifacts: dict[str, Any] | None = None,
    workspace_preview: dict[str, Any] | None = None,
    health_status: dict[str, Any] | None = None,
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
    if workspace_artifacts is not None:
        (artifact_base / "workspace-artifacts.json").write_text(
            json.dumps(workspace_artifacts, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if workspace_preview is not None:
        (artifact_base / "workspace-preview.json").write_text(
            json.dumps(workspace_preview, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    if health_status is not None:
        (artifact_base / "health.json").write_text(
            json.dumps(health_status, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    (artifact_base / "scorecard.md").write_text(
        render_scenario_scorecard(
            scenario=scenario,
            summary=summary,
            failures=failures,
            workspace_artifacts=workspace_artifacts,
            workspace_preview=workspace_preview,
            health_status=health_status,
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
    preflight_health = assert_observability_preflight(page)
    page.route(
        "**/api/chat/messages",
        lambda route: route.continue_(
            post_data=json.dumps(
                {
                    **json.loads(route.request.post_data or "{}"),
                    "scenario_id": scenario.name,
                    **({"model": MODEL_OVERRIDE} if MODEL_OVERRIDE else {}),
                    **(
                        {"tool_preset": scenario.tool_preset}
                        if scenario.tool_preset
                        else {}
                    ),
                    **(
                        {"research_depth": scenario.research_depth}
                        if scenario.research_depth
                        else {}
                    ),
                    **(
                        {"research_mode": scenario.research_mode}
                        if scenario.research_mode
                        else {}
                    ),
                    **(
                        {"research_profile": scenario.research_profile}
                        if scenario.research_profile
                        else {}
                    ),
                    **(
                        {"profile_source": scenario.profile_source}
                        if scenario.profile_source
                        else {}
                    ),
                },
                ensure_ascii=False,
            )
        ),
    )
    ids = send_message_and_capture_run_ids(page, scenario.prompt)
    run_id = ids.run_id
    if scenario.steering_message:
        queue_steering_message(page, run_id, scenario.steering_message)
    summary = wait_until_run_idle(
        page,
        scenario=scenario,
        run_id=run_id,
        timeout_ms=scenario.timeout_ms,
    )
    failures = assert_trace_acceptance(scenario, summary)
    workspace_artifacts: dict[str, Any] | None = None
    workspace_preview: dict[str, Any] | None = None
    health_status: dict[str, Any] | None = None
    try:
        health_status = fetch_health_status(page)
    except Exception as exc:
        failures.append(f"health status check failed: {exc}")
    health_status = health_status or preflight_health
    if scenario.required_artifact_path is not None:
        try:
            workspace_artifacts = fetch_workspace_artifacts(page, ids.session_id)
            artifact_paths = [
                item.get("path")
                for item in workspace_artifacts.get("artifacts", [])
                if isinstance(item, dict)
            ]
            if scenario.required_artifact_path not in artifact_paths:
                failures.append(
                    "required artifact missing from workspace API: "
                    f"{scenario.required_artifact_path}"
                )
            workspace_preview = fetch_workspace_artifact_preview(
                page,
                session_id=ids.session_id,
                path=scenario.required_artifact_path,
            )
            if scenario.required_artifact_preview and (
                scenario.required_artifact_preview
                not in str(workspace_preview.get("content") or "")
            ):
                failures.append(
                    "required artifact preview text missing: "
                    f"{scenario.required_artifact_preview}"
                )
        except Exception as exc:
            failures.append(f"workspace artifact API check failed: {exc}")
    failures = reconcile_workspace_artifact_failures(
        scenario=scenario,
        summary=summary,
        failures=failures,
        workspace_artifacts=workspace_artifacts,
    )
    if scenario.require_artifact_panel and not failures:
        try:
            assert_artifact_panel(page, scenario=scenario)
        except Exception as exc:
            failures.append(f"artifact panel check failed: {exc}")
    write_scenario_artifacts(
        page=page,
        scenario=scenario,
        summary=summary,
        failures=failures,
        workspace_artifacts=workspace_artifacts,
        workspace_preview=workspace_preview,
        health_status=health_status,
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
    parser.add_argument(
        "--model-preflight",
        action="store_true",
        help="Run the cheap model+web-tool ladder before full research.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    if args.all:
        scenario_names = sorted(SCENARIOS)
    elif args.model_preflight:
        scenario_names = list(MODEL_PREFLIGHT_SCENARIOS)
    else:
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
