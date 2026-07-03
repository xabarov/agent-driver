"""Declarative harness-profile layer: per-provider/model request shaping.

A :class:`~agent_driver.contracts.profiles.HarnessProfile` declares prompt
slots, tool exclusions and tool-description overrides for the models it
matches. The pure helpers here select the active profile and apply it at
request-assembly time, keeping the step loop and prompt templates untouched.
"""

from agent_driver.harness.profile import (
    apply_system_slots,
    apply_tool_overrides,
    profile_excluded_tools,
    select_harness_profile,
)
from agent_driver.harness.capability_packs import (
    build_capability_pack_dry_run,
    build_capability_pack_resolution,
    resolve_capability_pack,
    seed_adapter_manifests,
    seed_capability_packs,
    seed_deep_research_chat_demo_pack,
    seed_excel_workbook_chat_pack,
    seed_scenario_specs,
)
from agent_driver.harness.capability_runner import (
    run_capability_pack_deterministic_gates,
)
from agent_driver.harness.continuous_validation import (
    audit_validation_evidence,
    render_validation_markdown,
    seed_flake_records,
    seed_harness_baselines,
    seed_host_adoption_states,
    seed_release_gate_policies,
    write_validation_audit_report,
)

__all__ = [
    "apply_system_slots",
    "apply_tool_overrides",
    "audit_validation_evidence",
    "build_capability_pack_dry_run",
    "build_capability_pack_resolution",
    "profile_excluded_tools",
    "render_validation_markdown",
    "resolve_capability_pack",
    "run_capability_pack_deterministic_gates",
    "select_harness_profile",
    "seed_adapter_manifests",
    "seed_capability_packs",
    "seed_deep_research_chat_demo_pack",
    "seed_excel_workbook_chat_pack",
    "seed_flake_records",
    "seed_harness_baselines",
    "seed_host_adoption_states",
    "seed_release_gate_policies",
    "seed_scenario_specs",
    "write_validation_audit_report",
]
