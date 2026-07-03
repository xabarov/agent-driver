"""CLI commands for capability-pack dry-runs."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_driver.contracts.continuous_validation import FlakeRecord
from agent_driver.harness import (
    audit_validation_evidence,
    build_capability_pack_dry_run,
    run_capability_pack_deterministic_gates,
    write_validation_audit_report,
)
from agent_driver.runtime.validation import build_validation_gate_summary
from agent_driver.runtime.validation_artifacts import write_validation_artifacts


def capability_pack_command(args: argparse.Namespace) -> int:
    """Dispatch capability-pack subcommands."""
    if args.capability_pack_command == "dry-run":
        return capability_pack_dry_run_command(args)
    if args.capability_pack_command == "run-deterministic":
        return capability_pack_run_deterministic_command(args)
    if args.capability_pack_command == "audit":
        return capability_pack_audit_command(args)
    print(
        f"capability-pack error: unsupported subcommand {args.capability_pack_command}"
    )
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


def capability_pack_audit_command(args: argparse.Namespace) -> int:
    """Audit persisted capability-pack evidence without executing live gates."""
    try:
        payload = audit_validation_evidence(
            list(args.evidence_index_dir or []),
            baseline_ids=list(args.baseline_id or []),
            flake_records=_load_flake_records(getattr(args, "quarantine_file", None)),
            strict=bool(args.strict),
            no_live=bool(args.no_live),
        )
    except ValueError as exc:
        print(f"capability-pack error: {exc}")
        return 2

    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        paths = write_validation_audit_report(Path(output_dir), payload)
        payload["written_artifacts"] = paths

    print(json.dumps(payload, ensure_ascii=True, indent=2))
    if args.strict and not payload.get("strict_passed", False):
        return 1
    return 0


def capability_pack_run_deterministic_command(args: argparse.Namespace) -> int:
    """Run deterministic pack commands and persist command-output evidence."""
    try:
        payload = run_capability_pack_deterministic_gates(
            pack_id=args.pack_id,
            adapter_id=args.adapter_id,
            scenario_ids=list(args.scenario_id or []),
            deterministic_commands=(
                list(args.deterministic_command) if args.deterministic_command else None
            ),
            support_bundle_artifact_path=(
                "manifest.json" if getattr(args, "output_dir", None) else None
            ),
            cwd=args.cwd,
            timeout_seconds=args.timeout_seconds,
        )
    except ValueError as exc:
        print(f"capability-pack error: {exc}")
        return 2

    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        _write_execution_artifacts(Path(output_dir), payload)

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


def _write_execution_artifacts(output_dir: Path, payload: dict[str, Any]) -> None:
    evidence_index = payload.get("evidence_index")
    resolution = payload.get("capability_pack_resolution")
    validation_gates = payload.get("validation_gate_results")
    command_outputs = payload.get("executed_commands")
    write_validation_artifacts(
        output_dir,
        evidence_index=evidence_index if isinstance(evidence_index, dict) else None,
        validation_gates=(
            build_validation_gate_summary({"validation_gate_results": validation_gates})
            if isinstance(validation_gates, list)
            else None
        ),
        command_outputs=command_outputs if isinstance(command_outputs, list) else None,
        extra_json_artifacts={
            "capability_pack_resolution": (
                resolution if isinstance(resolution, dict) else {}
            ),
            "capability_pack_run": payload,
        },
    )


def _load_flake_records(path: str | None) -> list[FlakeRecord]:
    if not path:
        return []
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = payload.get("flakes") if isinstance(payload, dict) else payload
    if not isinstance(rows, list):
        raise ValueError("quarantine file must be a JSON list or {'flakes': [...]}")
    return [FlakeRecord.model_validate(row) for row in rows if isinstance(row, dict)]


__all__ = [
    "capability_pack_command",
    "capability_pack_audit_command",
    "capability_pack_dry_run_command",
    "capability_pack_run_deterministic_command",
]
