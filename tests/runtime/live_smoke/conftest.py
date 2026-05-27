"""Shared markers for optional live OpenRouter smoke tests.

Note on mark propagation: a ``pytestmark`` defined here does NOT
propagate to test items collected from sibling modules (pytest treats
conftest-level ``pytestmark`` as scoped to tests defined in *this*
file). To apply ``live`` to every test under this directory we use
``pytest_collection_modifyitems`` instead, which is the supported
hook for sub-tree mark injection.
"""

from __future__ import annotations

import pytest

from tests.support.live_harness import live_enabled

_LIVE_SKIP = pytest.mark.skipif(
    not live_enabled(),
    reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1",
)


def pytest_collection_modifyitems(config, items):  # pragma: no cover - hook
    """Apply ``live`` + ``asyncio`` + live-skip to every test in this subtree."""
    for item in items:
        if "live_smoke" in str(item.fspath):
            item.add_marker(pytest.mark.live)
            item.add_marker(pytest.mark.asyncio)
            item.add_marker(_LIVE_SKIP)
