"""Write Playwright validation-gate artifacts for policy supervision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.runtime.playwright_gate import write_playwright_gate_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/playwright-gate",
        help="Directory for Playwright validation artifacts.",
    )
    parser.add_argument(
        "--ui-claim",
        action="store_true",
        help="Set when this evidence backs a user-visible policy claim.",
    )
    parser.add_argument("--passed", action="store_true")
    parser.add_argument("--failed", action="store_true")
    parser.add_argument("--screenshot", action="append", default=[])
    parser.add_argument("--command", default="make test-chat-concepts")
    args = parser.parse_args()
    passed = None
    if args.passed:
        passed = True
    if args.failed:
        passed = False
    result = write_playwright_gate_artifacts(
        Path(args.output_dir),
        ui_claim=bool(args.ui_claim),
        passed=passed,
        screenshot_paths=[Path(item) for item in args.screenshot],
        command=args.command,
    )
    gate = result["gate"]
    print(
        json.dumps(
            {
                "gate_id": gate["gate_id"],
                "status": gate["status"],
                "reason": gate["reason"],
                "screenshot_count": gate["redacted_metadata"]["screenshot_count"],
                "artifact_count": result["artifact_manifest"]["artifact_count"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
