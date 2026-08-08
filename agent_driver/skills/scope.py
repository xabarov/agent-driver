"""Resolve a skill's declared ``allowed_tools`` for runtime tool-scoping (Skills S6).

A host can pin a run to a skill via ``tool_policy.metadata["skill_scope"] = "<name>"``.
The runtime then narrows the effective tool surface to that skill's ``allowed_tools`` so
the model only sees the tools the skill is meant to use. Enforcement is deterministic and
host-controlled — it is NOT triggered by the model merely reading a skill (there is no
"active skill" mode), which keeps the narrowing predictable and safe.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent_driver.skills.registry import list_skill_manifests


def resolve_skill_allowed_tools(
    sources: Sequence[str | Path],
    name: str,
    *,
    trusted_roots: Sequence[str | Path] = (),
    max_skills: int = 50,
) -> list[str] | None:
    """Return the named skill's declared ``allowed_tools``, or ``None``.

    ``None`` when the name is blank, no skill of that name is found in ``sources``, or the
    skill declares no ``allowed_tools`` — in every one of those cases there is nothing to
    scope to, so the caller applies no narrowing.
    """
    target = (name or "").strip()
    if not target:
        return None
    roots = tuple(Path(root).expanduser() for root in trusted_roots)
    for source in sources:
        try:
            manifests, _ = list_skill_manifests(
                base_dir=Path(source).expanduser(),
                trusted_roots=roots,
                max_results=max_skills,
            )
        except (OSError, ValueError):
            continue
        for manifest in manifests:
            if manifest.name == target:
                return list(manifest.allowed_tools) if manifest.allowed_tools else None
    return None


__all__ = ["resolve_skill_allowed_tools"]
