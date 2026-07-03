"""Capture Phoenix UI screenshots for product-shaped trace smokes."""

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
    parser.add_argument("--project-name", required=True)
    parser.add_argument("--smoke-file", required=True)
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    smoke = json.loads(Path(args.smoke_file).read_text())
    review = _capture_review(
        base_url=args.base_url.rstrip("/"),
        project_name=args.project_name,
        smoke=smoke,
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
        command=f"uv run python tools/policy_supervision/product_trace_ui_review.py --project-name {args.project_name}",
        reason=(
            "product_trace_ui_review_passed"
            if review["passed"]
            else "product_trace_ui_review_failed"
        ),
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        phoenix_run_ids=[smoke["trace_id"]] if smoke.get("trace_id") else None,
        playwright_screenshots=screenshot_sources,
        extra_json_artifacts={"product_trace_ui_review": review_artifact},
    )
    print(
        json.dumps(
            {
                "project_name": args.project_name,
                "trace_id": smoke.get("trace_id"),
                "passed": review["passed"],
                "failed_checks": review["failed_checks"],
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
    smoke: dict[str, Any],
) -> dict[str, Any]:
    trace_id = str(smoke["trace_id"])
    expected = [str(item) for item in smoke.get("expected_text") or []]
    screenshots: list[str] = []
    checks: dict[str, bool] = {}
    with tempfile.TemporaryDirectory(prefix="agent-driver-product-ui-") as tmp:
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
            page.goto(
                f"{project_spans_url}/{trace_id}",
                wait_until="networkidle",
                timeout=30000,
            )
            page.wait_for_timeout(1500)
            _screenshot(page, tmp_dir / "01-trace-root.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["trace_id_visible"] = trace_id in body
            checks["expected_tree_visible"] = all(text in body for text in expected)
            _select_span(page, expected[3])
            _screenshot(page, tmp_dir / "02-llm-plan.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["llm_panel_readable"] = (
                "Input Messages" in body
                and "Output Messages" in body
                and "product-smoke-model" in body
            )

            _select_span(page, expected[4])
            _screenshot(page, tmp_dir / "03-tool.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["tool_panel_readable"] = (
                "Tool:" in body and expected[4].split(".", 1)[1] in body
            )

            _select_span(page, expected[1])
            _screenshot(page, tmp_dir / "04-guardrail.png", screenshots)
            body = page.locator("body").inner_text(timeout=10000)
            checks["guardrail_panel_readable"] = (
                "guardrail" in body and expected[1] in body
            )
            browser.close()
        persisted = _persist_tmp_screenshots(screenshots)
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


def _persist_tmp_screenshots(screenshots: list[str]) -> list[str]:
    persisted_dir = Path(tempfile.mkdtemp(prefix="agent-driver-product-ui-keep-"))
    persisted: list[str] = []
    for screenshot in screenshots:
        source = Path(screenshot)
        target = persisted_dir / source.name
        target.write_bytes(source.read_bytes())
        persisted.append(str(target))
    return persisted


if __name__ == "__main__":
    raise SystemExit(main())
