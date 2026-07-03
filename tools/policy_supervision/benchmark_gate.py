"""Write benchmark validation-gate artifacts for policy supervision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.runtime.benchmark_gate import write_benchmark_gate_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/benchmark-gate",
        help="Directory for benchmark validation artifacts.",
    )
    parser.add_argument(
        "--benchmark-claim",
        action="store_true",
        help="Set when this evidence backs a quality/cost/latency claim.",
    )
    parser.add_argument("--passed", action="store_true")
    parser.add_argument("--failed", action="store_true")
    parser.add_argument("--benchmark-json")
    parser.add_argument("--benchmark-markdown")
    parser.add_argument("--command", default="make eval-regression")
    args = parser.parse_args()
    passed = None
    if args.passed:
        passed = True
    if args.failed:
        passed = False
    benchmark_json = None
    if args.benchmark_json:
        benchmark_json = json.loads(Path(args.benchmark_json).read_text(encoding="utf-8"))
    benchmark_markdown = None
    if args.benchmark_markdown:
        benchmark_markdown = Path(args.benchmark_markdown).read_text(encoding="utf-8")
    result = write_benchmark_gate_artifacts(
        Path(args.output_dir),
        benchmark_claim=bool(args.benchmark_claim),
        passed=passed,
        benchmark_json=benchmark_json,
        benchmark_markdown=benchmark_markdown,
        command=args.command,
    )
    gate = result["gate"]
    print(
        json.dumps(
            {
                "gate_id": gate["gate_id"],
                "status": gate["status"],
                "reason": gate["reason"],
                "report_present": gate["redacted_metadata"]["report_present"],
                "artifact_count": result["artifact_manifest"]["artifact_count"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
