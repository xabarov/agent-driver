"""Skills S3 — memoized manifest cache (parse once, invalidate on file change)."""

from __future__ import annotations

import os
import time

import pytest

from agent_driver.skills import clear_skill_manifest_cache
from agent_driver.skills.parser import load_skill_manifest


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_skill_manifest_cache()
    yield
    clear_skill_manifest_cache()


def _write(root, name: str, description: str) -> None:
    (root / name).mkdir(parents=True, exist_ok=True)
    (root / name / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {description}\n---\n# {name}\nbody\n",
        encoding="utf-8",
    )


def test_repeated_load_is_cached(tmp_path) -> None:
    _write(tmp_path, "s", "v1")
    f = tmp_path / "s" / "SKILL.md"
    m1 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    m2 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    # A cache hit returns the very same object (no re-parse).
    assert m1 is m2
    assert m1.description == "v1"


def test_different_trusted_roots_not_miscached(tmp_path) -> None:
    _write(tmp_path, "s", "v1")
    f = tmp_path / "s" / "SKILL.md"
    trusted = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    untrusted = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=())
    assert trusted.trusted is True
    assert untrusted.trusted is False
    assert trusted is not untrusted


def test_edit_invalidates_cache(tmp_path) -> None:
    _write(tmp_path, "s", "v1")
    f = tmp_path / "s" / "SKILL.md"
    m1 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    time.sleep(0.01)
    f.write_text(
        "---\nname: s\ndescription: v2_CHANGED\n---\n# s\nbody2\n", encoding="utf-8"
    )
    os.utime(f, None)
    m2 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    assert m2 is not m1
    assert m2.description == "v2_CHANGED"
    assert m2.digest != m1.digest


def test_clear_cache_forces_reparse(tmp_path) -> None:
    _write(tmp_path, "s", "v1")
    f = tmp_path / "s" / "SKILL.md"
    m1 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    clear_skill_manifest_cache()
    m2 = load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    assert m2 is not m1
    assert m2.description == m1.description  # same content → equal, fresh object


def test_uncached_parse_is_skipped_on_hit(tmp_path, monkeypatch) -> None:
    """A cache hit must not call the uncached parser again (the perf win)."""
    from agent_driver.skills import parser

    _write(tmp_path, "s", "v1")
    f = tmp_path / "s" / "SKILL.md"
    calls = {"n": 0}
    original = parser._load_skill_manifest_uncached

    def _counting(*args, **kwargs):
        calls["n"] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(parser, "_load_skill_manifest_uncached", _counting)
    load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    load_skill_manifest(f, base_dir=tmp_path, trusted_roots=(tmp_path,))
    assert calls["n"] == 1  # parsed once, served from cache twice
