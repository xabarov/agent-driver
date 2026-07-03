"""Write Phoenix validation-gate artifacts for policy supervision."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.runtime.phoenix_gate import write_phoenix_gate_artifacts


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/phoenix-gate",
        help="Directory for Phoenix validation artifacts.",
    )
    parser.add_argument(
        "--live-claim",
        action="store_true",
        help="Set when this evidence backs a live runtime/provider claim.",
    )
    parser.add_argument("--span-count", type=int, default=None)
    parser.add_argument("--trace-id", action="append", default=[])
    parser.add_argument("--endpoint", default="http://127.0.0.1:6006")
    parser.add_argument("--project-name", default="agent-driver")
    args = parser.parse_args()
    result = write_phoenix_gate_artifacts(
        Path(args.output_dir),
        live_claim=bool(args.live_claim),
        span_count=args.span_count,
        trace_ids=list(args.trace_id or []),
        endpoint=args.endpoint,
        project_name=args.project_name,
    )
    gate = result["gate"]
    print(
        json.dumps(
            {
                "gate_id": gate["gate_id"],
                "status": gate["status"],
                "reason": gate["reason"],
                "span_count": gate["redacted_metadata"]["span_count"],
                "trace_id_count": len(gate["redacted_metadata"]["trace_ids"]),
                "artifact_count": result["artifact_manifest"]["artifact_count"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
