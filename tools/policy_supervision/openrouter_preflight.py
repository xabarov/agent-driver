"""Run the policy-supervision OpenRouter preflight ladder."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agent_driver.runtime.openrouter_preflight import run_openrouter_preflight_ladder


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output-dir",
        default=".agent-driver/policy-supervision/openrouter-preflight",
        help="Directory for validation artifacts.",
    )
    parser.add_argument("--model", default=None, help="OpenRouter model id.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Run live healthcheck/completion when an OpenRouter key is configured.",
    )
    parser.add_argument(
        "--phoenix-endpoint",
        default=None,
        help="Phoenix base URL or OTLP HTTP /v1/traces endpoint for live spans.",
    )
    parser.add_argument(
        "--phoenix-project-name",
        default="agent-driver-policy-supervision",
        help="Phoenix project name for policy-supervision live spans.",
    )
    parser.add_argument(
        "--phoenix-gate-output-dir",
        default=None,
        help="Optional directory for phoenix_trace validation artifacts.",
    )
    args = parser.parse_args()
    result = asyncio.run(
        run_openrouter_preflight_ladder(
            output_dir=Path(args.output_dir),
            live=bool(args.live),
            model=args.model,
            phoenix_endpoint=args.phoenix_endpoint,
            phoenix_project_name=args.phoenix_project_name,
            phoenix_gate_output_dir=(
                Path(args.phoenix_gate_output_dir)
                if args.phoenix_gate_output_dir
                else None
            ),
        )
    )
    print(
        json.dumps(
            {
                "provider": result["provider"],
                "model": result["model"],
                "deterministic_status": result["provider_preflight"]["preflight"][
                    "status"
                ],
                "selected_action": result["request_shape_plan"]["selected_action"],
                "live_status": result["live_result"]["status"],
                "phoenix_trace_id_present": bool(
                    result["live_result"].get("phoenix_trace_id")
                ),
                "artifact_count": result["artifact_manifest"]["artifact_count"],
            },
            ensure_ascii=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
