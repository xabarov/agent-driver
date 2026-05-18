"""Shared markers for optional live OpenRouter smoke tests."""

from __future__ import annotations

import pytest

from tests.support.live_harness import live_enabled

pytestmark = [
    pytest.mark.live,
    pytest.mark.asyncio,
    pytest.mark.skipif(
        not live_enabled(),
        reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1",
    ),
]
