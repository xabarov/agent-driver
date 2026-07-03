"""CLI commands for capability-pack dry-runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_driver.harness import build_capability_pack_dry_run
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def capability_pack_command(args: argparse.Namespace) -> int:
    """Dispatch capability-pack subcommands."""
    if args.capability_pack_command == "dry-run":
        return capability_pack_dry_run_command(args)
    print(f"capability-pack error: unsupported subcommand {args.capability_pack_command}")
    return 2


def capability_pack_dry_run_command(args: argparse.Namespace) -> int:
    """Resolve a capability pack and optionally persist dry-run artifacts."""
    try:
        payload = build_capability_pack_dry_run(
            pack_id=args.pack_id,
            adapter_id=args.adapter_id,
            scenario_ids=list(args.scenario_id or []),
        )
    except ValueError as exc:
        print(f"capability-pack error: {exc}")
        return 2

    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        _write_dry_run_artifacts(Path(output_dir), payload)

    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def _write_dry_run_artifacts(output_dir: Path, payload: dict[str, Any]) -> None:
    evidence_index = payload.get("evidence_index")
    resolution = payload.get("capability_pack_resolution")
    write_validation_artifacts(
        output_dir,
        evidence_index=evidence_index if isinstance(evidence_index, dict) else None,
        extra_json_artifacts={
            "capability_pack_resolution": (
                resolution if isinstance(resolution, dict) else {}
            ),
            "capability_pack_dry_run": payload,
        },
    )


__all__ = ["capability_pack_command", "capability_pack_dry_run_command"]
