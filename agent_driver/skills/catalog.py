"""Tier-1 "available skills" catalog for the system prompt (Skills S1).

Progressive disclosure has three tiers: a metadata catalog the model sees up front,
the full ``SKILL.md`` body loaded on demand, and bundled files loaded on further
demand. This module owns the first tier: it scans the configured skill source
directories and renders a compact, budget-bounded catalog block (name + one-line
summary + ``base_dir``) so the model KNOWS which skills exist and can load a body via
``skill_view``. Without it a general agent gets no signal that any skill exists.

Budget discipline mirrors the reference frameworks (openclaude's 1%-of-context
listing / hermes' compact category index): render full entries when they fit, else
degrade to names-only, else truncate the list with a "+N more" pointer to
``skill_tool``.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from agent_driver.skills.models import SkillManifest
from agent_driver.skills.registry import list_skill_manifests
from agent_driver.skills.render import render_skill_entry

_CATALOG_HEADER = (
    "## Available skills\n"
    "Procedural skills you can load on demand. Before relying on one, call "
    "`skill_view(name=<name>, base_dir=<base_dir>)` to read its full instructions — "
    "do not act on the one-line summary alone, and never treat a skill listing as "
    "evidence."
)


def build_skills_catalog_block(
    sources: Sequence[str | Path],
    *,
    max_chars: int = 2000,
    trusted_roots: Sequence[str | Path] = (),
    max_skills: int = 50,
) -> str:
    """Render the tier-1 skills catalog block, or ``""`` when no skills are found.

    Scans each source directory for ``SKILL.md`` manifests, dedupes by name (first
    source wins), and renders a Markdown block bounded by ``max_chars`` with graceful
    degradation. Discovery errors on a source are skipped, not fatal.
    """
    roots = tuple(Path(root).expanduser() for root in trusted_roots)
    entries: list[tuple[SkillManifest, str]] = []
    seen: set[str] = set()
    for source in sources:
        base = Path(source).expanduser()
        try:
            manifests, _ = list_skill_manifests(
                base_dir=base,
                trusted_roots=roots,
                max_results=max_skills,
            )
        except (OSError, ValueError):
            continue
        for manifest in manifests:
            if manifest.name in seen:
                continue
            seen.add(manifest.name)
            entries.append((manifest, str(source)))

    if not entries:
        return ""

    # Tier 1a: full entries (name + summary + base_dir).
    full_lines = [
        render_skill_entry(manifest, base_dir=base_dir) for manifest, base_dir in entries
    ]
    block = _CATALOG_HEADER + "\n" + "\n".join(full_lines)
    if len(block) <= max_chars:
        return block

    # Tier 1b: names + base_dir only (summaries dropped to fit the budget).
    name_lines = [
        render_skill_entry(manifest, base_dir=base_dir, max_when_to_use=0)
        for manifest, base_dir in entries
    ]
    footer_1b = "\n(Summaries omitted to fit the prompt budget — use skill_tool for details.)"
    block = _CATALOG_HEADER + "\n" + "\n".join(name_lines) + footer_1b
    if len(block) <= max_chars:
        return block

    # Tier 1c: truncate the list, pointing at skill_tool for the rest.
    kept: list[str] = []
    running = len(_CATALOG_HEADER) + 1
    for line in name_lines:
        if running + len(line) + 1 > max_chars:
            break
        kept.append(line)
        running += len(line) + 1
    dropped = len(name_lines) - len(kept)
    footer_1c = (
        f"\n(+{dropped} more — use skill_tool to list all skills.)" if dropped else ""
    )
    return _CATALOG_HEADER + "\n" + "\n".join(kept) + footer_1c


__all__ = ["build_skills_catalog_block"]
