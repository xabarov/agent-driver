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

__all__ = [
    "apply_system_slots",
    "apply_tool_overrides",
    "build_capability_pack_dry_run",
    "build_capability_pack_resolution",
    "profile_excluded_tools",
    "resolve_capability_pack",
    "select_harness_profile",
    "seed_adapter_manifests",
    "seed_capability_packs",
    "seed_deep_research_chat_demo_pack",
    "seed_excel_workbook_chat_pack",
    "seed_scenario_specs",
]
