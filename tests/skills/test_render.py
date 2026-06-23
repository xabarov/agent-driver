"""Tests for the compact skill-listing renderer."""

from __future__ import annotations

from agent_driver.skills import SkillManifest, render_skill_entry


def _manifest(
    name: str = "data-cleanup",
    *,
    when_to_use: str | None = "Use before any aggregation or report.",
    description: str = "Clean a messy sheet.",
) -> SkillManifest:
    return SkillManifest(
        name=name,
        description=description,
        when_to_use=when_to_use,
        skill_dir="/skills/data-cleanup",
        path="/skills/data-cleanup/SKILL.md",
        digest="deadbeef",
    )


def test_renders_name_and_when_to_use() -> None:
    line = render_skill_entry(_manifest())
    assert line.startswith("- **data-cleanup**")
    assert "Use before any aggregation" in line


def test_caps_line_length_regardless_of_base_dir_path() -> None:
    # A very long absolute base_dir must not blow past the cap (the bug a
    # hand-rolled consumer renderer hit on a long install path).
    long_base = "/home/user/" + "deeply/" * 40 + "skills/curated"
    line = render_skill_entry(
        _manifest(when_to_use="x" * 500),
        base_dir=long_base,
        label="куратор",
        max_line_len=300,
    )
    assert len(line) <= 300
    assert line.endswith("…")


def test_truncates_when_to_use_and_collapses_newlines() -> None:
    line = render_skill_entry(
        _manifest(when_to_use="first line\nsecond line  " + "y" * 400),
        max_when_to_use=40,
    )
    # newline collapsed to a single space, summary truncated with an ellipsis
    assert "\n" not in line
    assert "first line second line" in line
    assert line.endswith("…")


def test_base_dir_shown_relative_to_root() -> None:
    line = render_skill_entry(
        _manifest(),
        base_dir="/srv/app/skills/curated",
        relative_to="/srv/app",
    )
    assert "base_dir=`skills/curated`" in line
    assert "/srv/app" not in line


def test_base_dir_falls_back_to_full_path_when_not_under_root() -> None:
    line = render_skill_entry(
        _manifest(),
        base_dir="/elsewhere/skills",
        relative_to="/srv/app",
    )
    assert "base_dir=`/elsewhere/skills`" in line


def test_description_used_when_no_when_to_use() -> None:
    line = render_skill_entry(_manifest(when_to_use=None))
    assert "Clean a messy sheet." in line


def test_label_included_in_meta() -> None:
    line = render_skill_entry(_manifest(), label="пользовательский")
    assert "_(пользовательский)_" in line
