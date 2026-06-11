"""Pure selection + application helpers for :class:`HarnessProfile`.

Kept dependency-light (stdlib + the contract) so the request builder can apply
a profile without importing runtime internals. Application is split into three
small, independently testable steps — model selection, system-prompt slots, and
the tool catalog (exclusions + description overrides) — so a caller can apply
exactly the parts it owns.
"""

from __future__ import annotations

from fnmatch import fnmatch
from typing import Any

from agent_driver.contracts.profiles import HarnessProfile


def select_harness_profile(
    profiles: tuple[HarnessProfile, ...], model: str | None
) -> HarnessProfile | None:
    """Return the first profile matching ``model`` (first-match wins).

    A profile with no ``match_models`` matches any model (provider-wide
    default). ``model`` is matched case-insensitively against each ``fnmatch``
    glob; an empty/None model only matches the no-pattern default.
    """
    target = (model or "").lower()
    for profile in profiles:
        if not profile.match_models:
            return profile
        if any(fnmatch(target, pattern.lower()) for pattern in profile.match_models):
            return profile
    return None


def apply_system_slots(system: str, profile: HarnessProfile) -> str:
    """Wrap the assembled system prompt in the profile's prefix/suffix slots."""
    parts = [
        profile.system_prefix.strip(),
        (system or "").strip(),
        profile.system_suffix.strip(),
    ]
    return "\n\n".join(part for part in parts if part)


def profile_excluded_tools(
    profile: HarnessProfile | None, existing_denied: tuple[str, ...] | None
) -> tuple[str, ...] | None:
    """Merge the profile's ``excluded_tools`` into an existing deny tuple.

    Returns ``existing_denied`` unchanged when there is no profile or it
    excludes nothing, so callers pass the result straight to the request-tool
    filter (where ``denied`` removes a tool from the model-visible catalog).
    """
    if profile is None or not profile.excluded_tools:
        return existing_denied
    merged = list(existing_denied or ())
    for name in profile.excluded_tools:
        if name not in merged:
            merged.append(name)
    return tuple(merged)


def apply_tool_overrides(
    tools: list[dict[str, Any]], profile: HarnessProfile
) -> list[dict[str, Any]]:
    """Rewrite tool descriptions per the profile, leaving other fields intact.

    Operates on the provider tool-schema shape
    (``{"type": "function", "function": {"name", "description", ...}}``) and
    returns a new list — the input dicts are never mutated. Override keys that
    match no surfaced tool are simply ignored (the tool may be excluded or
    absent for this model).
    """
    overrides = profile.tool_description_overrides
    if not overrides:
        return tools
    result: list[dict[str, Any]] = []
    for tool in tools:
        function = tool.get("function")
        if isinstance(function, dict) and function.get("name") in overrides:
            new_function = {**function, "description": overrides[function["name"]]}
            result.append({**tool, "function": new_function})
        else:
            result.append(tool)
    return result


__all__ = [
    "apply_system_slots",
    "apply_tool_overrides",
    "profile_excluded_tools",
    "select_harness_profile",
]
