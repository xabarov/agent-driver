"""Opt-in live smoke checks for CLI OpenAI-compatible provider."""

from __future__ import annotations

import os

import pytest

from agent_driver.cli.providers import CliProviderConfig, build_cli_provider


def _has_live_env() -> bool:
    return bool(
        os.environ.get("AGENT_DRIVER_API_KEY")
        and os.environ.get("AGENT_DRIVER_BASE_URL")
        and os.environ.get("AGENT_DRIVER_MODEL")
    )


@pytest.mark.asyncio
@pytest.mark.skipif(not _has_live_env(), reason="live OpenAI-compatible env is not configured")
async def test_live_openai_provider_healthcheck_smoke() -> None:
    """Optional live healthcheck smoke; skipped without env."""
    provider = build_cli_provider(CliProviderConfig(provider="openrouter"))
    status = await provider.healthcheck()
    assert status.configured is True
