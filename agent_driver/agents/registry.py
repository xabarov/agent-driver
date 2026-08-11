"""Agent-definition registry with layered precedence (coordination C2).

Resolves an ``agent_type`` name to an :class:`AgentDefinition`, letting a host
override built-ins: register lower-priority sources first (built-ins), higher ones
last (project files, then programmatic), and a higher priority wins a name clash.
Within one priority the first registration wins, so a source can't silently shadow
its own earlier entries. Mirrors OpenHands' "first registration wins" precedence.
"""

from __future__ import annotations

from pathlib import Path

from agent_driver.agents.definition import AgentDefinition, load_agent_definitions


class AgentRegistry:
    """Name → :class:`AgentDefinition` map with priority-based override."""

    def __init__(self) -> None:
        self._by_name: dict[str, AgentDefinition] = {}
        self._priority: dict[str, int] = {}

    def register(self, definition: AgentDefinition, *, priority: int = 0) -> bool:
        """Register ``definition``; a higher ``priority`` overrides a name clash.

        Returns ``True`` if it was stored (new name, or it outranked the incumbent),
        ``False`` if an equal-or-higher-priority definition already held the name.
        """
        existing_priority = self._priority.get(definition.name)
        if existing_priority is not None and priority <= existing_priority:
            return False
        self._by_name[definition.name] = definition
        self._priority[definition.name] = priority
        return True

    def register_all(
        self, definitions: list[AgentDefinition], *, priority: int = 0
    ) -> None:
        """Register each definition at ``priority`` (order preserved)."""
        for definition in definitions:
            self.register(definition, priority=priority)

    def register_directory(
        self, directory: str | Path, *, priority: int = 0, source: str = "filesystem"
    ) -> None:
        """Load ``*.md`` agent files from ``directory`` and register them."""
        self.register_all(
            load_agent_definitions(directory, source=source), priority=priority
        )

    def get(self, name: str) -> AgentDefinition | None:
        """Return the definition registered under ``name``, or ``None``."""
        return self._by_name.get(name)

    def names(self) -> list[str]:
        """Return the registered agent-type names, sorted."""
        return sorted(self._by_name)

    def all(self) -> list[AgentDefinition]:
        """Return all registered definitions, name-sorted."""
        return [self._by_name[name] for name in self.names()]

    def __contains__(self, name: object) -> bool:
        return isinstance(name, str) and name in self._by_name

    def __len__(self) -> int:
        return len(self._by_name)


__all__ = ["AgentRegistry"]
