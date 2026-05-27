"""Phase 12 H21 — tests for tool dispatch metadata.

Pins:
* ``should_defer`` + ``always_load`` + ``aliases`` are stored on
  ``ToolManifest`` and round-trip through Pydantic;
* ``is_deferred()`` resolver — always_load wins over should_defer;
* alias validation rejects invalid names + duplicates;
* registry resolves aliases on ``get()``;
* registry blocks alias collisions with another tool's canonical name;
* re-registering a tool drops its stale aliases;
* ``list_non_deferred()`` filters deferred tools.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import ToolManifest
from agent_driver.tools import ToolRegistry


def _manifest(**overrides) -> ToolManifest:
    base = dict(name="t", description="t")
    base.update(overrides)
    return ToolManifest(**base)


async def _handler(_args):
    return {"summary": "ok"}


# -- contract ---------------------------------------------------------------


def test_defaults_dispatch_metadata_off():
    m = _manifest()
    assert m.should_defer is False
    assert m.always_load is False
    assert m.aliases == []
    assert m.is_deferred() is False


def test_should_defer_true_marks_deferred():
    m = _manifest(should_defer=True)
    assert m.is_deferred() is True


def test_always_load_overrides_should_defer():
    """always_load=True wins so operators can flip a tool back on
    without removing the defer marker on the manifest."""
    m = _manifest(should_defer=True, always_load=True)
    assert m.is_deferred() is False


def test_aliases_round_trip_through_pydantic():
    m = _manifest(aliases=["alt1", "alt2"])
    assert m.aliases == ["alt1", "alt2"]
    raw = m.model_dump()
    assert raw["aliases"] == ["alt1", "alt2"]
    restored = ToolManifest.model_validate(raw)
    assert restored.aliases == ["alt1", "alt2"]


def test_alias_invalid_name_rejected():
    with pytest.raises(ValidationError):
        _manifest(aliases=["with space"])
    with pytest.raises(ValidationError):
        _manifest(aliases=["bad/name"])


def test_alias_empty_string_rejected():
    with pytest.raises(ValidationError):
        _manifest(aliases=[""])


def test_alias_duplicates_rejected():
    with pytest.raises(ValidationError):
        _manifest(aliases=["a", "a"])


# -- registry ---------------------------------------------------------------


def test_registry_resolves_alias_to_same_tool():
    registry = ToolRegistry()
    registry.register(_manifest(name="read_file", aliases=["file_read"]), _handler)
    direct = registry.get("read_file")
    via_alias = registry.get("file_read")
    assert direct is not None
    assert via_alias is not None
    assert direct is via_alias  # same RegisteredTool instance


def test_registry_get_unknown_name_returns_none():
    registry = ToolRegistry()
    registry.register(_manifest(name="known"), _handler)
    assert registry.get("never_registered") is None


def test_registry_self_alias_is_silently_dropped():
    """An alias matching the canonical name is meaningless — registry
    accepts but doesn't add it to the alias index."""
    registry = ToolRegistry()
    registry.register(_manifest(name="solo", aliases=["solo"]), _handler)
    assert registry.list_aliases() == {}


def test_registry_alias_collision_with_canonical_blocks_registration():
    registry = ToolRegistry()
    registry.register(_manifest(name="file_read"), _handler)
    with pytest.raises(ValueError, match="collides with another tool"):
        registry.register(
            _manifest(name="cat", aliases=["file_read"]),
            _handler,
        )


def test_registry_reregistration_drops_stale_aliases():
    """Re-registering a tool with a different alias list cleans up
    the old aliases so they no longer resolve."""
    registry = ToolRegistry()
    registry.register(_manifest(name="file_read", aliases=["read_file"]), _handler)
    assert registry.get("read_file") is not None
    registry.register(_manifest(name="file_read", aliases=["fetch_file"]), _handler)
    assert registry.get("read_file") is None
    assert registry.get("fetch_file") is not None
    assert registry.get("file_read") is not None


def test_registry_list_non_deferred_excludes_deferred_tools():
    registry = ToolRegistry()
    registry.register(_manifest(name="always_visible"), _handler)
    registry.register(_manifest(name="bulky_mcp", should_defer=True), _handler)
    registry.register(
        _manifest(name="critical", should_defer=True, always_load=True),
        _handler,
    )
    visible_names = {tool.manifest.name for tool in registry.list_non_deferred()}
    assert visible_names == {"always_visible", "critical"}


def test_registry_list_registered_includes_deferred_tools():
    """Discovery / search paths still see deferred tools."""
    registry = ToolRegistry()
    registry.register(_manifest(name="visible"), _handler)
    registry.register(_manifest(name="hidden", should_defer=True), _handler)
    all_names = {tool.manifest.name for tool in registry.list_registered()}
    assert all_names == {"visible", "hidden"}


def test_registry_list_aliases_returns_snapshot():
    registry = ToolRegistry()
    registry.register(_manifest(name="read_file", aliases=["file_read", "cat"]), _handler)
    snapshot = registry.list_aliases()
    assert snapshot == {"file_read": "read_file", "cat": "read_file"}
    # Mutating the snapshot doesn't affect the registry.
    snapshot.clear()
    assert registry.list_aliases() == {"file_read": "read_file", "cat": "read_file"}


def test_registry_aliases_work_with_governed_executor_lookup():
    """Spot-check that ``get()`` integration in the governed executor
    path (used via ``_lookup_manifest`` for H12 partition) sees aliases.
    """
    registry = ToolRegistry()
    registry.register(_manifest(name="canonical", aliases=["alias_a"]), _handler)
    # Simulate executor's lookup pattern.
    manifest_a = registry.get("alias_a").manifest if registry.get("alias_a") else None
    assert manifest_a is not None
    assert manifest_a.name == "canonical"
