"""Playwright validation gate tests."""

from __future__ import annotations

from agent_driver.runtime.playwright_gate import (
    build_playwright_ui_gate,
    write_playwright_gate_artifacts,
)


def test_playwright_gate_skips_without_ui_claim() -> None:
    gate = build_playwright_ui_gate(ui_claim=False, passed=True)

    assert gate.status == "skipped"
    assert gate.reason == "no_user_visible_policy_claim"


def test_playwright_gate_fails_pass_without_screenshot_for_ui_claim() -> None:
    gate = build_playwright_ui_gate(ui_claim=True, passed=True)

    assert gate.status == "failed"
    assert gate.reason == "playwright_screenshot_missing_for_ui_claim"


def test_playwright_gate_passes_and_persists_screenshot(tmp_path) -> None:
    screenshot = tmp_path / "policy-ui.png"
    screenshot.write_bytes(b"fake-png")

    result = write_playwright_gate_artifacts(
        tmp_path / "playwright",
        ui_claim=True,
        passed=True,
        screenshot_paths=[screenshot],
        command="make test-chat-concepts",
    )

    assert result["gate"]["status"] == "passed"
    assert result["validation_gates"]["statuses"]["playwright_ui"] == "passed"
    assert (tmp_path / "playwright" / "playwright_screenshots" / "policy-ui.png").is_file()
