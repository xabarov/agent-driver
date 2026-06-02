"""Tests for bundled research skills."""

from __future__ import annotations

from agent_driver.skills import CURATED_RESEARCH_SKILL_NAMES, curated_skills_dir
from agent_driver.skills.registry import list_skill_manifests


def test_curated_research_skills_are_discoverable_as_metadata() -> None:
    """Bundled research skills should be discoverable through normal registry."""
    manifests, truncated = list_skill_manifests(
        base_dir=curated_skills_dir(),
        trusted_roots=(curated_skills_dir(),),
    )

    assert not truncated
    assert {manifest.name for manifest in manifests} == set(
        CURATED_RESEARCH_SKILL_NAMES
    )
    assert all(manifest.trusted for manifest in manifests)
    assert "web_fetch" in {
        tool for manifest in manifests for tool in manifest.allowed_tools
    }


def test_deep_research_skill_uses_real_artifact_and_subagent_tools() -> None:
    manifests, _ = list_skill_manifests(
        base_dir=curated_skills_dir(),
        trusted_roots=(curated_skills_dir(),),
    )
    deep_research = next(
        manifest for manifest in manifests if manifest.name == "deep-research-report"
    )

    assert "run_subagent" not in deep_research.allowed_tools
    assert {
        "agent_tool",
        "read_file",
        "file_write",
        "file_edit",
        "file_patch",
        "artifact_preview",
    }.issubset(set(deep_research.allowed_tools))
