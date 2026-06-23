"""Render skill manifests into compact, length-bounded prompt lines.

Consumers that list skills in a system prompt (e.g. an embedding app's
"available skills" block) otherwise hand-roll the formatting and tend to embed
an absolute ``base_dir`` path, so a single entry can blow past a line-length
budget on a long install path. :func:`render_skill_entry` owns that formatting:
it collapses ``when_to_use`` to one line, shows ``base_dir`` relative to a root
when given, and hard-caps the whole line so the result never exceeds
``max_line_len`` regardless of path length.
"""

from __future__ import annotations

from pathlib import Path

from agent_driver.skills.models import SkillManifest

_ELLIPSIS = "…"


def _relativize(base_dir: Path | str, relative_to: Path | str | None) -> str:
    base = Path(base_dir)
    if relative_to is None:
        return base.as_posix()
    try:
        return base.resolve().relative_to(Path(relative_to).resolve()).as_posix()
    except ValueError:
        # Not under relative_to — fall back to the full path rather than guess.
        return base.as_posix()


def _truncate(text: str, limit: int) -> str:
    if limit <= 0:
        return ""
    if len(text) <= limit:
        return text
    if limit == 1:
        return _ELLIPSIS
    return text[: limit - 1].rstrip() + _ELLIPSIS


def render_skill_entry(
    manifest: SkillManifest,
    *,
    base_dir: Path | str | None = None,
    relative_to: Path | str | None = None,
    label: str | None = None,
    max_line_len: int = 300,
    max_when_to_use: int = 220,
) -> str:
    """Render one skill manifest as a single compact Markdown list line.

    The line is ``- **<name>** _(<label>, base_dir=`<dir>`)_ — <summary>`` where
    the summary is ``when_to_use`` (falling back to ``description``) collapsed to
    a single line and truncated to ``max_when_to_use``. ``base_dir`` is shown
    relative to ``relative_to`` when the former is under the latter. The returned
    line is guaranteed not to exceed ``max_line_len`` characters.
    """
    summary = " ".join((manifest.when_to_use or manifest.description or "").split())
    summary = _truncate(summary, max_when_to_use)

    meta_bits: list[str] = []
    if label:
        meta_bits.append(label)
    if base_dir is not None:
        meta_bits.append(f"base_dir=`{_relativize(base_dir, relative_to)}`")
    meta = f" _({', '.join(meta_bits)})_" if meta_bits else ""

    head = f"- **{manifest.name}**{meta}"
    line = f"{head} — {summary}" if summary else head
    return _truncate(line, max_line_len)


__all__ = ["render_skill_entry"]
