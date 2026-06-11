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

__all__ = [
    "apply_system_slots",
    "apply_tool_overrides",
    "profile_excluded_tools",
    "select_harness_profile",
]
