"""Capture Phoenix UI screenshots for live OpenRouter trace scenarios."""

from __future__ import annotations

import argparse
import json
import tempfile
from pathlib import Path
from typing import Any

from playwright.sync_api import Page, sync_playwright

from agent_driver.runtime.playwright_gate import build_playwright_ui_gate
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6006")
    parser.add_argument("--project-name", default="agent-driver-policy-supervision")
    parser.add_argument(
        "--scenario-file",
        default=".agent-driver/policy-supervision/openrouter-trace-scenarios/openrouter_trace_scenarios.json",
    )
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/openrouter-trace-ui-review",
    )
    args = parser.parse_args()

    payload = json.loads(Path(args.scenario_file).read_text())
    review = _capture_review(
        base_url=args.base_url.rstrip("/"),
        project_name=args.project_name,
        scenarios=payload.get("scenarios") or [],
    )
    output_dir = Path(args.output_dir)
    screenshot_sources = [Path(path) for path in review["screenshots"]]
    artifact_screenshots = [
        f"playwright_screenshots/{source.name}" for source in screenshot_sources
    ]
    review_artifact = {**review, "screenshots": artifact_screenshots}
    gate = build_playwright_ui_gate(
        ui_claim=True,
        passed=review["passed"],
        screenshot_paths=artifact_screenshots,
        command="make policy-supervision-openrouter-trace-ui-review",
        reason=(
            "openrouter_trace_ui_review_passed"
            if review["passed"]
            else "openrouter_trace_ui_review_failed"
        ),
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        phoenix_run_ids=[
            scenario["trace_id"]
            for scenario in payload.get("scenarios") or []
            if scenario.get("trace_id")
        ],
        playwright_screenshots=screenshot_sources,
        extra_json_artifacts={"openrouter_trace_ui_review": review_artifact},
    )
    print(
        json.dumps(
            {
                "passed": review["passed"],
                "failed_checks": review["failed_checks"],
                "scenario_count": len(review["scenario_reviews"]),
                "screenshot_count": len(artifact_screenshots),
                "artifact_count": manifest["artifact_count"],
                "output_dir": str(output_dir),
            },
            ensure_ascii=True,
        )
    )
    return 0 if review["passed"] else 1


def _capture_review(
    *,
    base_url: str,
    project_name: str,
    scenarios: list[dict[str, Any]],
) -> dict[str, Any]:
    checks: dict[str, bool] = {"scenario_count": len(scenarios) >= 3}
    scenario_reviews: list[dict[str, Any]] = []
    screenshots: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent-driver-openrouter-ui-") as tmp:
        tmp_dir = Path(tmp)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(f"{base_url}/projects", wait_until="networkidle", timeout=30000)
            checks["project_visible"] = _body_contains(page, project_name)
            page.get_by_text(project_name, exact=True).click()
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(1000)
            project_spans_url = page.url.split("?")[0].rstrip("/")
            for scenario in scenarios:
                scenario_reviews.append(
                    _review_scenario(
                        page=page,
                        project_spans_url=project_spans_url,
                        tmp_dir=tmp_dir,
                        screenshots=screenshots,
                        scenario=scenario,
                    )
                )
            browser.close()
        persisted = _persist_tmp_screenshots(screenshots)
    for scenario_review in scenario_reviews:
        checks[f"{scenario_review['scenario_id']}_passed"] = scenario_review["passed"]
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "base_url": base_url,
        "project_name": project_name,
        "checks": checks,
        "failed_checks": failed,
        "passed": not failed,
        "scenario_reviews": scenario_reviews,
        "screenshots": persisted,
        "redaction": {"safe_by_default": True},
    }


def _review_scenario(
    *,
    page: Page,
    project_spans_url: str,
    tmp_dir: Path,
    screenshots: list[str],
    scenario: dict[str, Any],
) -> dict[str, Any]:
    scenario_id = str(scenario["scenario_id"])
    trace_id = str(scenario["trace_id"])
    page.goto(f"{project_spans_url}/{trace_id}", wait_until="networkidle", timeout=30000)
    page.wait_for_timeout(1500)
    _screenshot(page, tmp_dir / f"{scenario_id}-01-root.png", screenshots)
    body = page.locator("body").inner_text(timeout=10000)
    checks = {
        "trace_id_visible": trace_id in body,
        "agent_root_visible": f"openrouter_scenario.{scenario_id}" in body,
        "llm_span_visible": f"llm.{scenario_id}" in body,
    }
    if scenario_id != "simple_completion":
        checks["guardrail_visible"] = _expected_guardrail(scenario_id) in body
    _select_span(page, f"llm.{scenario_id}")
    _screenshot(page, tmp_dir / f"{scenario_id}-02-llm.png", screenshots)
    body = page.locator("body").inner_text(timeout=10000)
    checks["llm_panel_readable"] = (
        "Input Messages" in body
        and "Output Messages" in body
        and "deepseek/deepseek-v4-flash" in body
    )
    checks["llm_tokens_visible"] = str(scenario["usage"]["total_tokens"]) in body
    guardrail = _expected_guardrail(scenario_id)
    if guardrail:
        _select_span(page, guardrail)
        _screenshot(page, tmp_dir / f"{scenario_id}-03-guardrail.png", screenshots)
        body = page.locator("body").inner_text(timeout=10000)
        checks["guardrail_panel_readable"] = guardrail in body
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "scenario_id": scenario_id,
        "trace_id": trace_id,
        "checks": checks,
        "failed_checks": failed,
        "passed": not failed,
    }


def _expected_guardrail(scenario_id: str) -> str:
    if scenario_id == "request_shape_tool_path":
        return "provider_request_shape_preflight"
    if scenario_id == "guardrail_missing_evidence":
        return "required_source_evidence"
    return ""


def _select_span(page: Page, name: str) -> None:
    page.get_by_text(name, exact=True).last.click()
    page.wait_for_timeout(1000)


def _screenshot(page: Page, path: Path, screenshots: list[str]) -> None:
    page.screenshot(path=str(path), full_page=True)
    screenshots.append(str(path))


def _body_contains(page: Page, text: str) -> bool:
    try:
        return text in page.locator("body").inner_text(timeout=10000)
    except Exception:
        return False


def _persist_tmp_screenshots(screenshots: list[str]) -> list[str]:
    persisted_dir = Path(tempfile.mkdtemp(prefix="agent-driver-openrouter-ui-keep-"))
    persisted: list[str] = []
    for screenshot in screenshots:
        source = Path(screenshot)
        target = persisted_dir / source.name
        target.write_bytes(source.read_bytes())
        persisted.append(str(target))
    return persisted


if __name__ == "__main__":
    raise SystemExit(main())
