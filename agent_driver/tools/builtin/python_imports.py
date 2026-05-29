"""Python sandbox import allowlists and scientific stack resolution."""

from __future__ import annotations

from typing import Protocol

PYTHON_STDLIB_IMPORTS: tuple[str, ...] = (
    "array",
    "bisect",
    "collections",
    "dataclasses",
    "decimal",
    "datetime",
    "fractions",
    "functools",
    "heapq",
    "json",
    "itertools",
    "math",
    "operator",
    "queue",
    "random",
    "re",
    "statistics",
    "string",
    "time",
    "types",
    "typing",
    "unicodedata",
    "uuid",
)

PYTHON_SCIENTIFIC_IMPORTS: tuple[str, ...] = ("numpy", "scipy", "pandas")


class PythonToolSettingsLike(Protocol):
    include_scientific_stack: bool
    default_imports: tuple[str, ...]


def resolve_python_default_imports(*, include_scientific: bool) -> tuple[str, ...]:
    """Build sorted unique allowlist for stdlib with optional scientific stack."""
    names = list(PYTHON_STDLIB_IMPORTS)
    if include_scientific:
        names.extend(PYTHON_SCIENTIFIC_IMPORTS)
    return tuple(sorted(set(names)))


def scientific_imports_enabled(settings: PythonToolSettingsLike) -> bool:
    """Return whether numpy/scipy/pandas are part of the default allowlist."""
    return bool(getattr(settings, "include_scientific_stack", True))


def effective_python_imports(settings: PythonToolSettingsLike) -> tuple[str, ...]:
    """Use explicit default_imports when set; otherwise resolve from include_scientific_stack."""
    explicit = getattr(settings, "default_imports", ()) or ()
    if explicit:
        return tuple(sorted(set(item.strip() for item in explicit if item.strip())))
    return resolve_python_default_imports(
        include_scientific=scientific_imports_enabled(settings)
    )


def parse_python_scientific_enabled(
    *,
    no_python_scientific: bool = False,
    env_value: str | None = None,
) -> bool:
    """Resolve scientific stack flag from CLI and environment."""
    if no_python_scientific:
        return False
    if env_value is not None:
        normalized = env_value.strip().lower()
        if normalized in {"0", "false", "no", "off"}:
            return False
        if normalized in {"1", "true", "yes", "on"}:
            return True
    return True


def missing_scientific_packages(imports: tuple[str, ...]) -> tuple[str, ...]:
    """Return scientific packages in allowlist that fail to import in this environment."""
    missing: list[str] = []
    for name in PYTHON_SCIENTIFIC_IMPORTS:
        if name not in imports:
            continue
        try:
            __import__(name)
        except ImportError:
            missing.append(name)
    return tuple(missing)


__all__ = [
    "PYTHON_SCIENTIFIC_IMPORTS",
    "PYTHON_STDLIB_IMPORTS",
    "effective_python_imports",
    "missing_scientific_packages",
    "parse_python_scientific_enabled",
    "resolve_python_default_imports",
    "scientific_imports_enabled",
]
