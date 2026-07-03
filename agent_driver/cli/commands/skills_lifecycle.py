"""CLI command for deterministic skills lifecycle artifacts."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from agent_driver.contracts.skills_lifecycle import (
    SkillCapabilityFilter,
    SkillSelectionRequest,
)
from agent_driver.skills import (
    build_skill_inventory_snapshot,
    build_skill_lifecycle_compatibility_report,
    build_skill_lifecycle_evidence_index,
    build_skill_lock_file,
    build_skill_selection_decisions,
    curated_skills_dir,
    diff_skill_inventories,
    read_skill_lock_file,
    write_skill_lifecycle_artifacts,
)


def skills_lifecycle_command(args: argparse.Namespace) -> int:
    """Dispatch skills-lifecycle subcommands."""
    if args.skills_lifecycle_command == "audit":
        return skills_lifecycle_audit_command(args)
    print(
        "skills-lifecycle error: unsupported subcommand "
        f"{args.skills_lifecycle_command}"
    )
    return 2


def skills_lifecycle_audit_command(args: argparse.Namespace) -> int:
    """Build deterministic skills lifecycle reports without live gates."""
    skills_dir = Path(args.skills_dir) if args.skills_dir else curated_skills_dir()
    product_family = args.product_family or _product_family_from_scenario(args.scenario)
    host_profile = args.host_profile or product_family
    snapshot = build_skill_inventory_snapshot(
        base_dir=skills_dir,
        trusted_roots=(skills_dir,),
        snapshot_id=f"{host_profile}-skills-inventory",
        max_results=args.max_results,
    )
    lockfile = build_skill_lock_file(snapshot, host_profile=host_profile)
    previous = (
        read_skill_lock_file(Path(args.previous_lock))
        if args.previous_lock
        else lockfile
    )
    diff = diff_skill_inventories(previous, lockfile)
    allowed_tools = sorted(
        {tool for record in snapshot.manifest_refs for tool in record.allowed_tools}
    )
    filter_spec = SkillCapabilityFilter(
        product_family=product_family,
        allowed_tools=allowed_tools,
        trusted_only=True,
    )
    request = SkillSelectionRequest(
        request_id=f"{host_profile}-skills-selection",
        task_intent=args.task_intent or args.scenario,
        host_profile=host_profile,
        capability_filter=filter_spec,
    )
    decisions = build_skill_selection_decisions(request, snapshot.manifest_refs)
    report = build_skill_lifecycle_compatibility_report(
        report_id=f"skills-lifecycle:{host_profile}",
        product_family=product_family,
        host_profile=host_profile,
        snapshot=snapshot,
        lockfile=lockfile,
        filters_applied=[filter_spec],
        selections_made=decisions,
        no_claims=[
            "live provider/Phoenix evidence is no_claim unless explicitly executed",
            "UI skill rows require Playwright evidence if changed",
            "quality/cost/latency claims require benchmark artifacts",
        ],
        evidence_refs=[
            "skills_inventory_snapshot.json",
            "skills_lock.json",
            "skills_reload_diff.json",
            "skills_compatibility_report.json",
            "skills_compatibility_report.md",
        ],
        metadata={
            "scenario": args.scenario,
            "no_runtime_behavior_change": True,
        },
    )
    evidence_index = build_skill_lifecycle_evidence_index(
        report,
        scenario_ids=[args.scenario],
        include_live_no_claim_gates=bool(args.no_live),
    )
    output_dir = Path(args.output_dir)
    paths = write_skill_lifecycle_artifacts(
        output_dir,
        snapshot=snapshot,
        lockfile=lockfile,
        diff=diff,
        report=report,
        evidence_index=evidence_index,
    )
    payload = {
        "mode": "deterministic",
        "scenario": args.scenario,
        "skills_dir": str(skills_dir),
        "snapshot": snapshot.model_dump(mode="json"),
        "lockfile": lockfile.model_dump(mode="json"),
        "reload_diff": diff.model_dump(mode="json"),
        "report": report.model_dump(mode="json"),
        "evidence_index": evidence_index.model_dump(mode="json"),
        "written_artifacts": paths,
        "redaction": {
            "safe_by_default": True,
            "contains_raw_skill_body": False,
            "contains_raw_supporting_files": False,
        },
    }
    print(json.dumps(payload, ensure_ascii=True, indent=2))
    return 0


def _product_family_from_scenario(scenario: str) -> str:
    if "excel" in scenario:
        return "excel_ai"
    if "chat_demo" in scenario or "research" in scenario:
        return "chat_demo"
    return "skills_lifecycle"


__all__ = ["skills_lifecycle_audit_command", "skills_lifecycle_command"]
