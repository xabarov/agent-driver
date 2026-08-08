"""Keyword-triggered skill surfacing (Skills S5).

The S1 catalog lists every skill each turn, but the model must still notice a relevant
one. A skill can declare ``keywords`` in its frontmatter; when the user's request mentions
any (whole-word, case-insensitive), the runtime adds a targeted nudge naming that skill and
how to load it — a proactive middle ground between the passive catalog and full auto-injection
(the body still loads on demand via ``skill_view``, preserving progressive disclosure).

This generalizes the previously hardcoded curated-research reminder into a config-driven,
per-skill trigger. Path-glob and slash-command triggers (openhands' other trigger kinds) are
left to the consumer: path-globs are repo-agent-specific, and slash invocation is a UI concern.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from pathlib import Path

from agent_driver.skills.models import SkillManifest
from agent_driver.skills.registry import list_skill_manifests


def _matched_keywords(keywords: Sequence[str], lowered_text: str) -> list[str]:
    hits: list[str] = []
    for keyword in keywords:
        needle = keyword.strip().lower()
        if not needle:
            continue
        if re.search(rf"(?<!\w){re.escape(needle)}(?!\w)", lowered_text):
            hits.append(keyword.strip())
    return hits


def build_skill_keyword_hints(
    sources: Sequence[str | Path],
    user_input: str,
    *,
    trusted_roots: Sequence[str | Path] = (),
    max_hints: int = 3,
    max_skills: int = 50,
) -> str:
    """Return a targeted hint block for skills whose keywords match ``user_input``.

    Empty when the input is blank, no skill declares matching keywords, or discovery
    finds nothing. Dedupes by skill name (first source wins) and caps at ``max_hints``.
    """
    lowered = (user_input or "").lower()
    if not lowered.strip():
        return ""
    roots = tuple(Path(root).expanduser() for root in trusted_roots)
    matched: list[tuple[SkillManifest, str, list[str]]] = []
    seen: set[str] = set()
    for source in sources:
        base = Path(source).expanduser()
        try:
            manifests, _ = list_skill_manifests(
                base_dir=base, trusted_roots=roots, max_results=max_skills
            )
        except (OSError, ValueError):
            continue
        for manifest in manifests:
            if manifest.name in seen or not manifest.keywords:
                continue
            hits = _matched_keywords(manifest.keywords, lowered)
            if hits:
                seen.add(manifest.name)
                matched.append((manifest, str(source), hits))

    if not matched:
        return ""
    matched = matched[:max_hints]
    lines = [
        "## Skills matching this request",
        "Your request mentions keywords tied to the skills below. Load the relevant one "
        "with skill_view before proceeding — do not act on the one-line summary alone.",
    ]
    for manifest, _source, hits in matched:
        lines.append(
            f"- **{manifest.name}** (matched: {', '.join(hits)}) — "
            f"skill_view(name={manifest.name!r}, base_dir={manifest.skill_dir!r})"
        )
    return "\n".join(lines)


__all__ = ["build_skill_keyword_hints"]
