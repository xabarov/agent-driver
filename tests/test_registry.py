"""Tests for the shared in-process Registry primitive."""

from __future__ import annotations

import pytest

from agent_driver.registry import Registry, RegistryError


def test_register_get_case_insensitive() -> None:
    reg: Registry[int] = Registry(kind="thing")
    reg.register("Alpha", 1)
    assert reg.get("alpha") == 1
    assert reg.try_get("ALPHA") == 1
    assert reg.try_get("missing") is None


def test_aliases_resolve_to_same_value() -> None:
    reg: Registry[str] = Registry()
    reg.register("primary", "v", aliases=("alt", "ALT2"))
    assert reg.get("alt") == "v"
    assert reg.get("alt2") == "v"


def test_duplicate_rejected_unless_replace() -> None:
    reg: Registry[int] = Registry(kind="thing")
    reg.register("k", 1)
    with pytest.raises(RegistryError, match="already registered"):
        reg.register("k", 2)
    reg.register("k", 2, replace=True)
    assert reg.get("k") == 2


def test_missing_get_raises() -> None:
    reg: Registry[int] = Registry(kind="widget")
    with pytest.raises(RegistryError, match="unknown widget"):
        reg.get("nope")


def test_values_are_identity_deduped() -> None:
    reg: Registry[str] = Registry()
    shared = "shared"
    reg.register("a", shared, aliases=("b",))
    reg.register("c", "other")
    assert reg.values() == [shared, "other"]


def test_clear() -> None:
    reg: Registry[int] = Registry()
    reg.register("k", 1)
    reg.clear()
    assert reg.try_get("k") is None
