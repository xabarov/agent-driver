#!/usr/bin/env python3
"""Run real chat-demo Deep Research profile matrix through Playwright.

This script imports the existing live Playwright probe and builds dynamic
scenarios from a benchmark manifest. It sends real messages through the chat UI,
captures screenshots, reads trace summaries, and then checks whether the
expected short answer appears in the transcript or report preview.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = (
    REPO_ROOT
    / "examples/chat-demo/frontend/tests/e2e/deep_research_benchmark_questions.json"
)
LIVE_PROBE_PATH = (
    REPO_ROOT / "examples/chat-demo/frontend/tests/e2e/chat_live_probe.py"
)

DEEP_RESEARCH_FAILURES = (
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
    "final_missing_source_links",
    "plan_todos_incomplete_on_final",
    "deep_research_no_report_artifact",
    "deep_research_no_source_ledger_artifact",
    "deep_research_full_report_rewrite",
    "deep_research_stale_report_edit",
    "deep_research_repeated_report_read",
    "deep_research_final_missing_report_reference",
    "deep_research_missing_initial_todo",
    "deep_research_skill_denied",
    "deep_research_low_verified_coverage",
    "deep_research_preliminary_final",
    "deep_research_repeated_search_args",
    "deep_research_search_without_fetch_progress",
    "deep_research_tool_entropy_high",
    "deep_research_phase_violation",
    "deep_research_long_final_after_report",
)


@dataclass(frozen=True, slots=True)
class MatrixResult:
    scenario: str
    profile: str
    question_id: str
    ok: bool
    run_id: str | None
    expected_found: bool
    error: str | None
    artifact_dir: str
    trace_summary: dict[str, Any] | None


def main() -> int:
    args = parse_args()
    manifest = load_manifest(args.manifest)
    profiles = parse_profiles(args.profiles)
    questions = select_questions(manifest, args.question_id, args.limit)
    matrix = [(profile, question) for profile in profiles for question in questions]
    if args.dry_run:
        for profile, question in matrix:
            print(f"{profile}\t{question['id']}\t{question['prompt'][:120]}")
        return 0

    live_probe = import_live_probe()
    results: list[MatrixResult] = []
    with live_probe.sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=args.headed is False)
        try:
            for profile, question in matrix:
                scenario = build_scenario(live_probe, profile, question)
                page = browser.new_page(viewport={"width": 1440, "height": 1000})
                try:
                    summary = live_probe.run_scenario(page, scenario)
                    artifact_dir = live_probe.ARTIFACT_DIR / scenario.name
                    expected_found = expected_answer_found(
                        artifact_dir,
                        str(question.get("expected_answer_regex") or ""),
                    )
                    error = None if expected_found else "expected answer not found"
                    results.append(
                        MatrixResult(
                            scenario=scenario.name,
                            profile=profile,
                            question_id=str(question["id"]),
                            ok=expected_found,
                            run_id=str(summary.get("run_id") or ""),
                            expected_found=expected_found,
                            error=error,
                            artifact_dir=str(artifact_dir),
                            trace_summary=summary,
                        )
                    )
                except Exception as exc:
                    artifact_dir = live_probe.ARTIFACT_DIR / scenario.name
                    summary = read_json_if_exists(artifact_dir / "trace-summary.json")
                    results.append(
                        MatrixResult(
                            scenario=scenario.name,
                            profile=profile,
                            question_id=str(question["id"]),
                            ok=False,
                            run_id=str(summary.get("run_id") or "") if summary else None,
                            expected_found=expected_answer_found(
                                artifact_dir,
                                str(question.get("expected_answer_regex") or ""),
                            ),
                            error=str(exc),
                            artifact_dir=str(artifact_dir),
                            trace_summary=summary,
                        )
                    )
                finally:
                    page.close()
        finally:
            browser.close()

    write_matrix_outputs(live_probe.ARTIFACT_DIR, results)
    print(render_matrix_markdown(results))
    return 0 if all(item.ok for item in results) else 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run live Deep Research profile matrix through chat-demo."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Benchmark question manifest.",
    )
    parser.add_argument(
        "--profiles",
        default="light,medium,hard",
        help="Comma-separated profiles: light,medium,hard.",
    )
    parser.add_argument(
        "--question-id",
        action="append",
        help="Question id to run. Can be passed multiple times.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of selected questions after filtering.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print matrix without launching browser or spending model tokens.",
    )
    parser.add_argument("--headed", action="store_true", help="Run Chromium headed.")
    return parser.parse_args()


def load_manifest(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def parse_profiles(raw: str) -> list[str]:
    values = [item.strip() for item in raw.split(",") if item.strip()]
    allowed = {"light", "medium", "hard"}
    invalid = [item for item in values if item not in allowed]
    if invalid:
        raise SystemExit(f"Unknown profile(s): {', '.join(invalid)}")
    return values or ["light"]


def select_questions(
    manifest: dict[str, Any],
    question_ids: list[str] | None,
    limit: int | None,
) -> list[dict[str, Any]]:
    questions = list(manifest.get("questions") or [])
    if question_ids:
        wanted = set(question_ids)
        questions = [item for item in questions if item.get("id") in wanted]
    if limit is not None:
        questions = questions[: max(0, limit)]
    if not questions:
        raise SystemExit("No questions selected.")
    return questions


def import_live_probe():
    spec = importlib.util.spec_from_file_location("chat_live_probe", LIVE_PROBE_PATH)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Could not import {LIVE_PROBE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["chat_live_probe"] = module
    spec.loader.exec_module(module)
    return module


def build_scenario(live_probe, profile: str, question: dict[str, Any]):
    prompt = str(question["prompt"])
    scenario_name = f"deep-{profile}-{question['id']}"
    if profile == "light":
        return live_probe.LiveScenario(
            name=scenario_name,
            prompt=prompt,
            required_tools=("web_search", "web_fetch"),
            forbidden_tools=(
                "agent_tool",
                "file_write",
                "file_edit",
                "file_patch",
                "bash",
                "python",
            ),
            tool_preset="web",
            min_research_fetch_count=1,
            timeout_ms=360000,
            requires_research=None,
        )
    if profile == "medium":
        return live_probe.LiveScenario(
            name=scenario_name,
            prompt=(
                f"{prompt}\n\n"
                "Use the medium Deep Research profile: bounded subagents for "
                "independent source discovery, write the report to "
                "research/report.md, keep chat concise, and cite fetched URLs."
            ),
            required_tools=(
                "todo_write",
                "agent_tool",
                "web_search",
                "web_fetch",
                "file_write",
                "read_file",
            ),
            forbidden_tools=("bash", "python"),
            tool_preset="deep_research",
            research_depth="deep_parallel_research",
            requires_subagent=True,
            min_research_fetch_count=3,
            min_research_domain_count=2,
            max_research_search_count_without_min_domains=12,
            max_research_fetch_count_without_min_domains=12,
            required_artifact_path="research/report.md",
            require_artifact_panel=True,
            require_research_efficiency=True,
            timeout_ms=720000,
            forbidden_failures=DEEP_RESEARCH_FAILURES,
            requires_research=True,
        )
    return live_probe.LiveScenario(
        name=scenario_name,
        prompt=(
            f"{prompt}\n\n"
            "Use the hard Deep Research profile: run source discovery, "
            "verification/audit, write research/report.md plus source/claim "
            "artifacts, patch rather than rewrite, and keep final chat concise."
        ),
        required_tools=(
            "todo_write",
            "agent_tool",
            "web_search",
            "web_fetch",
            "file_write",
            "read_file",
            "artifact_preview",
            "file_patch",
        ),
        forbidden_tools=("bash",),
        tool_preset="deep_research",
        research_depth="deep_parallel_research",
        requires_subagent=True,
        min_research_fetch_count=6,
        min_research_domain_count=3,
        max_research_search_count_without_min_domains=18,
        max_research_fetch_count_without_min_domains=18,
        required_artifact_path="research/report.md",
        require_artifact_panel=True,
        require_research_efficiency=True,
        timeout_ms=900000,
        forbidden_failures=DEEP_RESEARCH_FAILURES,
        requires_research=True,
    )


def expected_answer_found(artifact_dir: Path, expected_regex: str) -> bool:
    if not expected_regex:
        return True
    pattern = re.compile(expected_regex, re.IGNORECASE | re.MULTILINE)
    texts: list[str] = []
    for filename in ("transcript-excerpt.txt", "workspace-preview.json"):
        path = artifact_dir / filename
        if not path.is_file():
            continue
        try:
            texts.append(path.read_text(encoding="utf-8"))
        except UnicodeDecodeError:
            continue
    return any(pattern.search(text) for text in texts)


def write_matrix_outputs(artifact_dir: Path, results: list[MatrixResult]) -> None:
    artifact_dir.mkdir(parents=True, exist_ok=True)
    payload = [result_to_json(item) for item in results]
    (artifact_dir / "deep-research-matrix-summary.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (artifact_dir / "deep-research-matrix-scorecard.md").write_text(
        render_matrix_markdown(results),
        encoding="utf-8",
    )


def render_matrix_markdown(results: list[MatrixResult]) -> str:
    lines = [
        "# Deep Research Live Matrix",
        "",
        "| scenario | profile | ok | expected | tokens | tools | run | error |",
        "| --- | --- | --- | --- | ---: | --- | --- | --- |",
    ]
    for item in results:
        summary = item.trace_summary or {}
        llm = summary.get("llm") if isinstance(summary.get("llm"), dict) else {}
        usage = llm.get("usage") if isinstance(llm.get("usage"), dict) else {}
        tools = str(summary.get("tool_chain") or ",".join(summary.get("tool_names") or []))
        lines.append(
            "| "
            f"{item.scenario} | {item.profile} | {str(item.ok).lower()} | "
            f"{str(item.expected_found).lower()} | "
            f"{int(usage.get('total_tokens') or 0)} | "
            f"{tools or '-'} | {item.run_id or '-'} | "
            f"{(item.error or '-').replace('|', '/')[:180]} |"
        )
    lines.extend(["", "## Artifact Dirs", ""])
    for item in results:
        lines.append(f"- `{item.scenario}`: `{item.artifact_dir}`")
    return "\n".join(lines)


def result_to_json(item: MatrixResult) -> dict[str, Any]:
    return {
        "scenario": item.scenario,
        "profile": item.profile,
        "question_id": item.question_id,
        "ok": item.ok,
        "run_id": item.run_id,
        "expected_found": item.expected_found,
        "error": item.error,
        "artifact_dir": item.artifact_dir,
    }


def read_json_if_exists(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


if __name__ == "__main__":
    raise SystemExit(main())
