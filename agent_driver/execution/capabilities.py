"""Capability-aware routing helpers (EPIC-02).

Pure, deterministic functions over an :class:`ExecutionCapabilitySnapshot`:
derive the bounded request-only environment brief, and decide whether a tool's
declared execution requirement is satisfied. No I/O, no clock — the snapshot is
the sole source of truth, and "unknown" never satisfies a hard requirement.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from agent_driver.contracts.execution import (
    CapabilityName,
    CapabilityState,
    EnvironmentBrief,
    ExecutionCapabilitySnapshot,
    RequirementCheck,
    ToolExecutionRequirement,
)

if TYPE_CHECKING:
    from agent_driver.execution.protocol import ExecutionBackend

# Default character budget for the projected brief (before truncation).
DEFAULT_BRIEF_MAX_CHARS = 1200


def unknown_snapshot(backend_id: str) -> ExecutionCapabilitySnapshot:
    """An all-``UNKNOWN`` snapshot: the fail-safe when a backend reports nothing.
    Every hard requirement then fails closed — absence of evidence is not
    support."""
    return ExecutionCapabilitySnapshot(
        backend_id=backend_id or "unknown",
        environment_revision="unknown",
    )


async def resolve_capability_snapshot(
    backend: "ExecutionBackend",
) -> ExecutionCapabilitySnapshot:
    """Fetch a backend's capability snapshot, failing safe to all-``UNKNOWN``.

    A backend that does not implement ``capabilities`` (EPIC-01 minimal backend)
    or whose handshake raises yields an ``UNKNOWN`` snapshot rather than an
    optimistic default, so capability-gated tools stay withheld.
    """
    backend_id = getattr(backend, "backend_id", "unknown")
    fetch = getattr(backend, "capabilities", None)
    if not callable(fetch):
        return unknown_snapshot(backend_id)
    try:
        snapshot = await fetch()
    # pylint: disable=broad-exception-caught
    except Exception:  # noqa: BLE001 - fail safe: any handshake fault -> UNKNOWN
        return unknown_snapshot(backend_id)
    if not isinstance(snapshot, ExecutionCapabilitySnapshot):
        return unknown_snapshot(backend_id)
    return snapshot


def _program_line(name: str, version: str | None) -> str:
    return f"{name} {version}".rstrip() if version else name


def derive_environment_brief(
    snapshot: ExecutionCapabilitySnapshot,
    *,
    max_chars: int = DEFAULT_BRIEF_MAX_CHARS,
) -> EnvironmentBrief:
    """Project a snapshot into a deterministic, bounded, redaction-safe brief.

    Supported and degraded capabilities are listed (sorted) so the model sees a
    truthful summary; unknown/unsupported capabilities are intentionally omitted
    rather than described as available. Programs and limitations are trimmed to
    fit ``max_chars`` and ``truncated`` is set when anything was dropped.
    """
    supported = tuple(
        sorted(
            name.value
            for name, status in snapshot.capabilities.items()
            if status.state is CapabilityState.SUPPORTED
        )
    )
    degraded = tuple(
        sorted(
            name.value
            for name, status in snapshot.capabilities.items()
            if status.state is CapabilityState.DEGRADED
        )
    )
    programs = tuple(_program_line(p.name, p.version) for p in snapshot.programs)
    limitations = tuple(snapshot.limitations)

    revision = snapshot.digest or snapshot.environment_revision

    # Deterministic truncation: keep the fixed capability lists, trim the
    # variable-length program/limitation lists from the tail until within budget.
    truncated = False

    def _size(progs: tuple[str, ...], lims: tuple[str, ...]) -> int:
        return sum(len(x) for x in supported + degraded + progs + lims)

    while _size(programs, limitations) > max_chars and (programs or limitations):
        truncated = True
        if len(programs) >= len(limitations) and programs:
            programs = programs[:-1]
        elif limitations:
            limitations = limitations[:-1]
        else:  # pragma: no cover - defensive
            break

    return EnvironmentBrief(
        backend_id=snapshot.backend_id,
        capability_revision=revision,
        supported=supported,
        degraded=degraded,
        limitations=limitations,
        programs=programs,
        truncated=truncated,
    )


def render_environment_brief_text(brief: EnvironmentBrief) -> str:
    """Render a brief as a compact, deterministic, request-only prompt block.

    Only non-empty sections appear. Unknown/unsupported capabilities are never
    described as available (they are already omitted from the brief). The text
    is guidance for the model, explicitly not an authorization boundary.
    """
    lines = [
        f"Execution environment (capability revision {brief.capability_revision}):"
    ]
    if brief.supported:
        lines.append(f"- available capabilities: {', '.join(brief.supported)}")
    if brief.degraded:
        lines.append(f"- degraded capabilities: {', '.join(brief.degraded)}")
    if brief.programs:
        lines.append(f"- available programs: {', '.join(brief.programs)}")
    if brief.limitations:
        lines.append(f"- limitations: {'; '.join(brief.limitations)}")
    if brief.truncated:
        lines.append("- (environment summary truncated)")
    lines.append(
        "Tools requiring capabilities not listed here are hidden. This is "
        "guidance about the prepared environment, not an authorization boundary."
    )
    return "\n".join(lines)


def check_requirement(
    snapshot: ExecutionCapabilitySnapshot,
    requirement: ToolExecutionRequirement,
) -> RequirementCheck:
    """Decide whether ``requirement`` is satisfied by ``snapshot``.

    A HARD requirement is satisfied only when every named capability is
    observed ``SUPPORTED``; ``DEGRADED``/``UNSUPPORTED``/``UNKNOWN`` all fail
    closed (absence of evidence is not support). A SOFT requirement never blocks
    — it is informational — so it is always reported satisfied.
    """
    if not requirement.required:
        return RequirementCheck(satisfied=True)

    unmet: list[CapabilityName] = []
    for cap in requirement.required:
        if snapshot.status_of(cap).state is not CapabilityState.SUPPORTED:
            unmet.append(cap)

    if not unmet:
        return RequirementCheck(satisfied=True)

    detail = ", ".join(
        f"{cap.value}={snapshot.status_of(cap).state.value}" for cap in unmet
    )
    reason = f"unmet capabilities: {detail}"[:200]
    if not requirement.hard:
        # Soft: surface the reason but do not block.
        return RequirementCheck(satisfied=True, reason=reason, unmet=tuple(unmet))
    return RequirementCheck(satisfied=False, reason=reason, unmet=tuple(unmet))


def check_manifest_requirement(
    manifest: object,
    snapshot: ExecutionCapabilitySnapshot | None,
) -> RequirementCheck | None:
    """Check a tool manifest's execution requirement against a snapshot.

    Returns ``None`` (skip — the tool is unaffected) when there is no snapshot
    in scope (no backend injected) or the manifest declares no requirement.
    Otherwise returns the :class:`RequirementCheck`; a hard requirement is
    unsatisfied unless every named capability is ``SUPPORTED``.
    """
    if snapshot is None:
        return None
    requirement = getattr(manifest, "execution_requirement", None)
    if requirement is None or not isinstance(requirement, ToolExecutionRequirement):
        return None
    if not requirement.required:
        return None
    return check_requirement(snapshot, requirement)


def tool_is_withheld(
    manifest: object,
    snapshot: ExecutionCapabilitySnapshot | None,
) -> bool:
    """True when a hard, unmet requirement means the tool must not be exposed
    or dispatched. Soft/absent requirements and no-snapshot never withhold."""
    check = check_manifest_requirement(manifest, snapshot)
    return check is not None and not check.satisfied


def capability_diagnostics(
    snapshot: ExecutionCapabilitySnapshot,
    *,
    withheld_tools: tuple[str, ...] = (),
    brief: EnvironmentBrief | None = None,
) -> dict[str, object]:
    """A redaction-safe diagnostics payload for capability selection.

    Backend id, capability revision, observed status counts, and the names of
    tools withheld this step — never snapshot metadata values or secrets. Safe
    to place on request metadata / events / traces.
    """
    return {
        "backend_id": snapshot.backend_id,
        "environment_revision": snapshot.environment_revision,
        "capability_revision": (
            brief.capability_revision
            if brief is not None
            else (snapshot.digest or snapshot.environment_revision)
        ),
        "lease_generation": snapshot.lease_generation,
        "supported": list(brief.supported) if brief is not None else [],
        "degraded": list(brief.degraded) if brief is not None else [],
        "withheld_tools": list(withheld_tools),
        "brief_truncated": bool(brief.truncated) if brief is not None else False,
    }


__all__ = [
    "DEFAULT_BRIEF_MAX_CHARS",
    "unknown_snapshot",
    "resolve_capability_snapshot",
    "derive_environment_brief",
    "check_requirement",
    "check_manifest_requirement",
    "tool_is_withheld",
    "render_environment_brief_text",
    "capability_diagnostics",
]
