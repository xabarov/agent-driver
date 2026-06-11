"""A tiny, reusable in-process registry.

Several subsystems keep a module-global ``dict`` plus hand-rolled
register/get/list/reset helpers (provider descriptors, and good candidates
elsewhere). :class:`Registry` is the shared primitive for that pattern:
case-insensitive keys, alias support, duplicate protection, and identity-
deduped value listing. Callers wrap its errors in their own domain error type
so messages stay specific.
"""

from __future__ import annotations

from typing import Generic, TypeVar

T = TypeVar("T")


class RegistryError(KeyError):
    """Raised on a duplicate registration or a missing lookup key."""


class Registry(Generic[T]):
    """Case-insensitive keyed registry with alias support."""

    def __init__(self, *, kind: str = "item") -> None:
        self._kind = kind
        self._items: dict[str, T] = {}

    @staticmethod
    def _normalize(key: str) -> str:
        return (key or "").strip().lower()

    def register(
        self,
        key: str,
        value: T,
        *,
        aliases: tuple[str, ...] = (),
        replace: bool = False,
    ) -> None:
        """Register ``value`` under ``key`` and any ``aliases``."""
        for raw in (key, *aliases):
            normalized = self._normalize(raw)
            if not normalized:
                continue
            if normalized in self._items and not replace:
                raise RegistryError(f"{self._kind} already registered: {normalized!r}")
            self._items[normalized] = value

    def try_get(self, key: str) -> T | None:
        """Return the value for ``key``/alias, or ``None``."""
        return self._items.get(self._normalize(key))

    def get(self, key: str) -> T:
        """Return the value for ``key``/alias, or raise :class:`RegistryError`."""
        value = self.try_get(key)
        if value is None:
            raise RegistryError(f"unknown {self._kind}: {key!r}")
        return value

    def values(self) -> list[T]:
        """Return distinct registered values, first-registered order preserved."""
        seen: set[int] = set()
        result: list[T] = []
        for value in self._items.values():
            if id(value) not in seen:
                seen.add(id(value))
                result.append(value)
        return result

    def clear(self) -> None:
        """Drop all registrations."""
        self._items.clear()


__all__ = ["Registry", "RegistryError"]
