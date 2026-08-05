"""agent-driver package."""

from importlib.metadata import PackageNotFoundError, version as _pkg_version

from agent_driver import contracts
from agent_driver import sdk

# U7 (epic 055) — single-source runtime version. Prefer the installed
# distribution metadata (authoritative: comes from pyproject at build/install
# time); fall back to the pyproject value for a source tree without dist
# metadata. Kept in sync with ``pyproject.toml`` ``[project] version`` — a test
# (``tests/test_version.py``) asserts they agree so they can never silently drift.
_FALLBACK_VERSION = "0.4.1"
try:
    __version__ = _pkg_version("agent-driver")
except PackageNotFoundError:  # pragma: no cover - source tree without metadata
    __version__ = _FALLBACK_VERSION

__all__ = ["__version__", "contracts", "sdk"]
