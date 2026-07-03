"""Capture Phoenix UI screenshots for a known trace id."""

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


EXPECTED_TRACE_TEXT = [
    "policy_supervision.agent_run",
    "policy.required_source_evidence",
    "subagent.research",
    "llm.plan",
    "tool.source_lookup",
    "llm.final",
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:6006")
    parser.add_argument("--project-name", default="agent-driver-policy-supervision")
    parser.add_argument("--trace-id", default=None)
    parser.add_argument(
        "--trace-id-file",
        default=".agent-driver/policy-supervision/phoenix-smoke/phoenix_run_ids.json",
    )
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/phoenix-ui-review",
    )
    args = parser.parse_args()

    trace_id = args.trace_id or _load_trace_id(Path(args.trace_id_file))
    review = _capture_review(
        base_url=args.base_url.rstrip("/"),
        project_name=args.project_name,
        trace_id=trace_id,
    )
    output_dir = Path(args.output_dir)
    screenshot_sources = [Path(path) for path in review["screenshots"]]
    artifact_screenshots = [
        f"playwright_screenshots/{source.name}" for source in screenshot_sources
    ]
    review_artifact = {**review, "screenshots": artifact_screenshots}
    command = (
        "uv run python tools/policy_supervision/phoenix_ui_review.py "
        f"--trace-id {trace_id}"
    )
    gate = build_playwright_ui_gate(
        ui_claim=True,
        passed=review["passed"],
        screenshot_paths=artifact_screenshots,
        command=command,
        reason=(
            "phoenix_ui_review_passed"
            if review["passed"]
            else "phoenix_ui_review_failed"
        ),
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        playwright_screenshots=screenshot_sources,
        extra_json_artifacts={"phoenix_ui_review": review_artifact},
    )
    print(
        json.dumps(
            {
                "trace_id": trace_id,
                "passed": review["passed"],
                "failed_checks": review["failed_checks"],
                "screenshot_count": len(review["screenshots"]),
                "gate_status": gate.status,
                "artifact_count": manifest["artifact_count"],
                "output_dir": str(output_dir),
            },
            ensure_ascii=True,
        )
    )
    return 0 if review["passed"] else 1


def _load_trace_id(path: Path) -> str:
    payload = json.loads(path.read_text())
    run_ids = payload.get("run_ids")
    if not isinstance(run_ids, list) or not run_ids:
        raise ValueError(f"no run_ids found in {path}")
    return str(run_ids[0])


def _capture_review(
    *,
    base_url: str,
    project_name: str,
    trace_id: str,
) -> dict[str, Any]:
    checks: dict[str, bool] = {}
    screenshots: list[str] = []
    with tempfile.TemporaryDirectory(prefix="agent-driver-phoenix-ui-") as tmp:
        tmp_dir = Path(tmp)
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page(viewport={"width": 1440, "height": 1000})
            page.goto(f"{base_url}/projects", wait_until="networkidle", timeout=30000)
            _screenshot(page, tmp_dir / "01-projects.png", screenshots)
            checks["project_visible"] = _body_contains(page, project_name)
            page.get_by_text(project_name, exact=True).click()
            page.wait_for_load_state("networkidle", timeout=30000)
            page.wait_for_timeout(1000)
            project_spans_url = page.url.split("?")[0].rstrip("/")
            _screenshot(page, tmp_dir / "02-project-spans.png", screenshots)
            checks["agent_root_visible"] = _body_contains(
                page, "policy_supervision.agent_run"
            )

            page.goto(
                f"{project_spans_url}/{trace_id}",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_timeout(1500)
            _screenshot(page, tmp_dir / "03-trace-root.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["trace_id_visible"] = trace_id in body
            checks["expected_tree_visible"] = all(
                expected in body for expected in EXPECTED_TRACE_TEXT
            )
            checks["agent_panel_visible"] = "agent\npolicy_supervision.agent_run" in body

            _select_span(page, "llm.plan")
            _screenshot(page, tmp_dir / "04-llm-plan.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["llm_messages_visible"] = (
                "Input Messages" in body
                and "Output Messages" in body
                and "Plan a one-source evidence check." in body
                and "Call the source lookup tool once." in body
            )
            checks["llm_tokens_visible"] = "20" in body and "smoke-model" in body

            _select_span(page, "tool.source_lookup")
            _screenshot(page, tmp_dir / "05-tool-source-lookup.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["tool_panel_visible"] = (
                "Tool: source_lookup" in body
                and "phoenix trace smoke" in body
                and "redacted synthetic source" in body
            )

            _select_span(page, "policy.required_source_evidence")
            _screenshot(page, tmp_dir / "06-guardrail.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["guardrail_panel_visible"] = (
                "guardrail\npolicy.required_source_evidence" in body
                and "source_evidence" in body
            )
            browser.close()

        persisted = _persist_tmp_screenshots(tmp_dir, screenshots)
    failed = [name for name, ok in checks.items() if not ok]
    return {
        "base_url": base_url,
        "project_name": project_name,
        "trace_id": trace_id,
        "checks": checks,
        "failed_checks": failed,
        "passed": not failed,
        "screenshots": persisted,
        "redaction": {"safe_by_default": True},
    }


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


def _persist_tmp_screenshots(tmp_dir: Path, screenshots: list[str]) -> list[str]:
    persisted_dir = Path(tempfile.mkdtemp(prefix="agent-driver-phoenix-ui-keep-"))
    persisted: list[str] = []
    for screenshot in screenshots:
        source = Path(screenshot)
        target = persisted_dir / source.name
        target.write_bytes((tmp_dir / source.name).read_bytes())
        persisted.append(str(target))
    return persisted


if __name__ == "__main__":
    raise SystemExit(main())
