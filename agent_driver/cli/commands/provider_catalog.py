"""CLI commands for deterministic provider catalog compatibility evidence."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.llm.provider_catalog import (
    build_provider_compatibility_report,
    write_provider_catalog_artifacts,
)


def provider_catalog_command(args: argparse.Namespace) -> int:
    """Dispatch provider-catalog subcommands."""
    if args.provider_catalog_command == "audit":
        return provider_catalog_audit_command(args)
    print(f"provider-catalog error: unsupported subcommand {args.provider_catalog_command}")
    return 2


def provider_catalog_audit_command(args: argparse.Namespace) -> int:
    """Build deterministic provider plugin/catalog/routing artifacts."""
    if bool(args.live):
        print("provider-catalog error: live provider audit is not implemented")
        return 2
    report = build_provider_compatibility_report(
        report_id=args.scenario or "provider_catalog.deterministic.v1",
        include_hosts=True,
    )
    output_dir = Path(args.output_dir)
    written = write_provider_catalog_artifacts(output_dir, report)
    payload = {
        "mode": "deterministic",
        "scenario": args.scenario,
        "live_requested": False,
        "report": report.model_dump(mode="json"),
        "written_artifacts": written,
        "redaction": {
            "safe_by_default": True,
            "contains_secret_values": False,
            "contains_raw_provider_response": False,
        },
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


__all__ = ["provider_catalog_command", "provider_catalog_audit_command"]
