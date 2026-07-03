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
from agent_driver.harness.adapter_protocol import (
    build_harness_adapter_capability,
    build_harness_adapter_compatibility_report,
    project_harness_adapter_artifacts,
    project_harness_adapter_events,
    project_harness_adapter_run,
    project_harness_adapter_session,
    project_harness_support_bundle_refs,
    render_harness_adapter_compatibility_markdown,
    seed_harness_adapter_compatibility_reports,
    write_harness_adapter_compatibility_artifacts,
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
from agent_driver.harness.lifecycle_hooks import (
    build_lifecycle_hook_compatibility_report,
    project_lifecycle_hook_audit_events,
    seed_lifecycle_hook_audit_records,
    seed_lifecycle_hook_compatibility_reports,
    seed_lifecycle_hook_registrations,
)
from agent_driver.harness.durable_lifecycle import (
    DurableLifecycleRepository,
    build_durable_lifecycle_compatibility_report,
    render_durable_lifecycle_compatibility_markdown,
    seed_durable_lifecycle_compatibility_reports,
    seed_durable_lifecycle_repository,
    write_durable_lifecycle_artifacts,
)
from agent_driver.llm.provider_catalog import (
    build_provider_compatibility_report,
    render_provider_compatibility_markdown,
    seed_provider_plugin_registry,
    write_provider_catalog_artifacts,
)
from agent_driver.skills.lifecycle import (
    seed_chat_demo_skill_lifecycle_report,
    seed_excel_skill_lifecycle_report,
)

__all__ = [
    "apply_system_slots",
    "apply_tool_overrides",
    "audit_validation_evidence",
    "build_capability_pack_dry_run",
    "build_capability_pack_resolution",
    "build_harness_adapter_capability",
    "build_harness_adapter_compatibility_report",
    "build_durable_lifecycle_compatibility_report",
    "build_lifecycle_hook_compatibility_report",
    "build_provider_compatibility_report",
    "DurableLifecycleRepository",
    "profile_excluded_tools",
    "project_harness_adapter_artifacts",
    "project_harness_adapter_events",
    "project_harness_adapter_run",
    "project_harness_adapter_session",
    "project_harness_support_bundle_refs",
    "project_lifecycle_hook_audit_events",
    "render_validation_markdown",
    "render_durable_lifecycle_compatibility_markdown",
    "render_harness_adapter_compatibility_markdown",
    "render_provider_compatibility_markdown",
    "resolve_capability_pack",
    "run_capability_pack_deterministic_gates",
    "select_harness_profile",
    "seed_adapter_manifests",
    "seed_capability_packs",
    "seed_deep_research_chat_demo_pack",
    "seed_excel_workbook_chat_pack",
    "seed_chat_demo_skill_lifecycle_report",
    "seed_excel_skill_lifecycle_report",
    "seed_flake_records",
    "seed_harness_baselines",
    "seed_harness_adapter_compatibility_reports",
    "seed_host_adoption_states",
    "seed_durable_lifecycle_compatibility_reports",
    "seed_durable_lifecycle_repository",
    "seed_lifecycle_hook_audit_records",
    "seed_lifecycle_hook_compatibility_reports",
    "seed_lifecycle_hook_registrations",
    "seed_provider_plugin_registry",
    "seed_release_gate_policies",
    "seed_scenario_specs",
    "write_validation_audit_report",
    "write_durable_lifecycle_artifacts",
    "write_harness_adapter_compatibility_artifacts",
    "write_provider_catalog_artifacts",
]
