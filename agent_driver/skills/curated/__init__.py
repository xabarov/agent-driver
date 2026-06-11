"""Bundled curated skills shipped with agent-driver."""

from __future__ import annotations

from pathlib import Path

CURATED_RESEARCH_SKILL_NAMES = (
    "deep-research-report",
    "source-triangulation",
    "provider-doc-research",
    "literature-review",
    "citation-auditor",
)


def curated_skills_dir() -> Path:
    """Return the filesystem root containing bundled curated skills."""
    return Path(__file__).resolve().parent


__all__ = ["CURATED_RESEARCH_SKILL_NAMES", "curated_skills_dir"]
