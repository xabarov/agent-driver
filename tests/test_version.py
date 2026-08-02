"""U7 (epic 055) — runtime __version__ is single-sourced and pre-1.0.

Guards that ``agent_driver.__version__`` exists, is a valid pre-1.0 version, and
agrees with ``pyproject.toml`` ``[project] version`` so the runtime version, the
package metadata, and the (future) wheel can never silently drift.
"""

from __future__ import annotations

import re
import tomllib
from pathlib import Path

import agent_driver

_PYPROJECT = Path(__file__).resolve().parent.parent / "pyproject.toml"


def _pyproject_version() -> str:
    data = tomllib.loads(_PYPROJECT.read_text(encoding="utf-8"))
    return data["project"]["version"]


def test_version_attribute_exists_and_is_string() -> None:
    assert isinstance(agent_driver.__version__, str)
    assert agent_driver.__version__


def test_version_matches_pyproject() -> None:
    assert agent_driver.__version__ == _pyproject_version()


def test_version_is_valid_pre_1_0() -> None:
    version = agent_driver.__version__
    # PEP 440-ish: N.N.N with optional pre-release suffix; pre-1.0 (0.x).
    assert re.match(r"^0\.\d+\.\d+([abrc].*|rc\d+.*)?$", version), version
