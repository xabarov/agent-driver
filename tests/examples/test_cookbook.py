"""Smoke-run every cookbook example so the examples cannot rot."""

from __future__ import annotations

import asyncio
import importlib.util
from pathlib import Path

import pytest

_COOKBOOK = Path(__file__).resolve().parents[2] / "examples" / "cookbook"
_SCRIPTS = sorted(p.name for p in _COOKBOOK.glob("[0-9]*.py"))

# Top-level modules shipped only by optional `[extra]` installs. An example that
# imports one of these should be skipped — not failed — when the extra is absent,
# so the suite still runs on a base install. A ModuleNotFoundError for anything
# *not* in this set is a real breakage and must surface.
_OPTIONAL_MODULES = frozenset(
    {
        "acp",  # [acp]
        "starlette",  # [server]
        "uvicorn",  # [server]
        "psycopg",  # [postgres]
        "prompt_toolkit",  # [cli]
        "rich",  # [cli]
        "instructor",  # [instructor]
        "opentelemetry",  # observability extra
        "playwright",  # [dev]
    }
)


def _load_main(script: str):
    spec = importlib.util.spec_from_file_location(
        f"cookbook_{script[:-3]}", _COOKBOOK / script
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    try:
        spec.loader.exec_module(module)
    except ModuleNotFoundError as exc:
        if exc.name in _OPTIONAL_MODULES:
            pytest.skip(f"optional dependency {exc.name!r} not installed")
        raise
    return module.main


def test_cookbook_scripts_present() -> None:
    # Guard against an empty glob silently passing the parametrized test.
    assert len(_SCRIPTS) >= 8


@pytest.mark.parametrize("script", _SCRIPTS)
def test_cookbook_example_runs(script: str) -> None:
    main = _load_main(script)
    if asyncio.iscoroutinefunction(main):
        asyncio.run(main())
    else:
        main()
