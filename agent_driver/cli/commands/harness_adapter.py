"""CLI commands for deterministic harness-adapter compatibility reports."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from agent_driver.contracts.capability_packs import EvidenceArtifactIndex
from agent_driver.harness import (
    build_harness_adapter_compatibility_report,
    project_harness_adapter_events,
    write_harness_adapter_compatibility_artifacts,
)
from agent_driver.runtime.stream import project_runtime_events
from agent_driver.runtime.storage.factory import RuntimeStoreFactoryConfig


def harness_adapter_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Any,
    create_runtime_store_bundle: Any,
) -> int:
    """Dispatch harness-adapter subcommands."""
    if args.harness_adapter_command == "compat":
        return harness_adapter_compat_command(
            args,
            store_config_from_args=store_config_from_args,
            create_runtime_store_bundle=create_runtime_store_bundle,
        )
    print(
        f"harness-adapter error: unsupported subcommand {args.harness_adapter_command}"
    )
    return 2


def harness_adapter_compat_command(
    args: argparse.Namespace,
    *,
    store_config_from_args: Any,
    create_runtime_store_bundle: Any,
) -> int:
    """Build a no-live adapter compatibility report from offline evidence."""
    try:
        evidence_index = _load_evidence_index(Path(args.evidence_index_dir))
        stream_events = _load_stream_events(
            args,
            store_config_from_args=store_config_from_args,
            create_runtime_store_bundle=create_runtime_store_bundle,
        )
        projected_events = project_harness_adapter_events(
            stream_events,
            session_id=args.session_id,
            source="replay",
        )
        report = build_harness_adapter_compatibility_report(
            adapter_id=args.adapter,
            events=stream_events,
            evidence_index=evidence_index,
            session_id=args.session_id,
            no_live=bool(args.no_live),
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"harness-adapter error: {exc}")
        return 2

    output_dir = getattr(args, "output_dir", None)
    if output_dir:
        paths = write_harness_adapter_compatibility_artifacts(
            Path(output_dir),
            report,
            events=projected_events,
        )
        payload = report.model_dump(mode="json")
        payload["written_artifacts"] = paths
    else:
        payload = report.model_dump(mode="json")

    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def _load_evidence_index(path: Path) -> EvidenceArtifactIndex:
    index_path = path / "evidence_index.json" if path.is_dir() else path
    if not index_path.is_file():
        raise ValueError(f"missing evidence_index.json: {path}")
    return EvidenceArtifactIndex.model_validate(
        json.loads(index_path.read_text(encoding="utf-8"))
    )


def _load_stream_events(
    args: argparse.Namespace,
    *,
    store_config_from_args: Any,
    create_runtime_store_bundle: Any,
):
    run_id = getattr(args, "run_id", None)
    if not run_id:
        return []
    config: RuntimeStoreFactoryConfig = store_config_from_args(args)
    bundle = create_runtime_store_bundle(config)
    return project_runtime_events(bundle.event_log.list_for_run(run_id))


__all__ = [
    "harness_adapter_command",
    "harness_adapter_compat_command",
]
