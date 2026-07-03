"""Playwright validation-gate helpers for user-visible policy evidence."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from agent_driver.contracts.policy import ValidationGateResult
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def build_playwright_ui_gate(
    *,
    ui_claim: bool,
    passed: bool | None = None,
    screenshot_paths: list[str | Path] | None = None,
    command: str | None = None,
    reason: str | None = None,
) -> ValidationGateResult:
    """Return Playwright UI validation result for visible policy changes."""

    screenshots = [str(path) for path in (screenshot_paths or []) if str(path)]
    if not ui_claim:
        status = "skipped"
        resolved_reason = reason or "no_user_visible_policy_claim"
    elif passed is None:
        status = "not_run"
        resolved_reason = reason or "playwright_result_missing"
    elif not passed:
        status = "failed"
        resolved_reason = reason or "playwright_ui_failed"
    elif not screenshots:
        status = "failed"
        resolved_reason = reason or "playwright_screenshot_missing_for_ui_claim"
    else:
        status = "passed"
        resolved_reason = reason or "playwright_ui_passed_with_screenshots"

    return ValidationGateResult(
        gate_id="playwright_ui",
        status=status,
        reason=resolved_reason,
        command=command,
        redacted_metadata={
            "ui_claim": ui_claim,
            "screenshot_count": len(screenshots),
            "screenshots": screenshots,
        },
    )


def write_playwright_gate_artifacts(
    output_dir: str | Path,
    *,
    ui_claim: bool,
    passed: bool | None = None,
    screenshot_paths: list[str | Path] | None = None,
    command: str | None = None,
    reason: str | None = None,
) -> dict[str, Any]:
    """Persist Playwright gate evidence and return artifact manifest/result."""

    gate = build_playwright_ui_gate(
        ui_claim=ui_claim,
        passed=passed,
        screenshot_paths=screenshot_paths,
        command=command,
        reason=reason,
    )
    validation_gates = build_validation_gate_summary(
        {"validation_gates": [gate.model_dump(mode="json")]}
    )
    manifest = write_validation_artifacts(
        output_dir,
        validation_gates=validation_gates,
        playwright_screenshots=screenshot_paths,
    )
    return {
        "gate": gate.model_dump(mode="json"),
        "validation_gates": validation_gates,
        "artifact_manifest": manifest,
    }


__all__ = ["build_playwright_ui_gate", "write_playwright_gate_artifacts"]
