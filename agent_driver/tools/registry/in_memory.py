"""Tool registry contracts and lookup helpers."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.contracts.tools import ToolManifest
from agent_driver.tools.registry.types import ToolHandler


@dataclass(frozen=True, slots=True)
class RegisteredTool:
    """Immutable tuple of manifest and async handler."""

    manifest: ToolManifest
    handler: ToolHandler


class ToolRegistry:
    """In-memory registry for governed tools.

    Phase 12 H21 — registry stores tools by canonical manifest name AND
    by every alias declared on the manifest. ``get(name)`` resolves both
    primary and alias spellings to the same ``RegisteredTool``.
    """

    def __init__(self) -> None:
        self._items: dict[str, RegisteredTool] = {}
        # Phase 12 H21 — alias → canonical name lookup map.
        self._alias_index: dict[str, str] = {}

    def register(self, manifest: ToolManifest, handler: ToolHandler) -> None:
        """Register or replace a tool by canonical manifest name.

        Phase 12 H21 — also indexes every alias from
        ``manifest.aliases``. Re-registering a tool replaces both the
        canonical entry and removes stale aliases from a previous
        registration of the same canonical name.
        """
        # Drop stale aliases pointing at this canonical name (e.g. when
        # re-registering with a different alias list).
        stale_aliases = [
            alias
            for alias, canonical in self._alias_index.items()
            if canonical == manifest.name
        ]
        for alias in stale_aliases:
            self._alias_index.pop(alias, None)
        self._items[manifest.name] = RegisteredTool(manifest=manifest, handler=handler)
        for alias in manifest.aliases:
            if alias == manifest.name:
                # Self-alias is meaningless; ignore silently.
                continue
            if alias in self._items:
                # Don't shadow a canonical tool with someone else's alias.
                raise ValueError(
                    f"alias {alias!r} collides with another tool's canonical "
                    f"name (registered as {manifest.name!r})"
                )
            self._alias_index[alias] = manifest.name

    def get(self, tool_name: str) -> RegisteredTool | None:
        """Return registered tool by canonical name OR alias."""
        direct = self._items.get(tool_name)
        if direct is not None:
            return direct
        canonical = self._alias_index.get(tool_name)
        if canonical is None:
            return None
        return self._items.get(canonical)

    def list_names(self) -> list[str]:
        """Return sorted list of registered canonical tool names."""
        return sorted(self._items)

    def list_registered(self) -> list[RegisteredTool]:
        """Return registered tool records sorted by canonical manifest name."""
        return [self._items[name] for name in sorted(self._items)]

    def list_non_deferred(self) -> list[RegisteredTool]:
        """Phase 12 H21 — registered tools whose manifest is NOT deferred.

        Use for default agent enumeration; callers that want every tool
        regardless of deference (e.g. ``catalog_search``) still call
        ``list_registered()``.
        """
        return [
            tool for tool in self.list_registered() if not tool.manifest.is_deferred()
        ]

    def list_aliases(self) -> dict[str, str]:
        """Phase 12 H21 — read-only snapshot of the alias → canonical map."""
        return dict(self._alias_index)


__all__ = ["RegisteredTool", "ToolRegistry"]
