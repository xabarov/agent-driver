"""E4: tool concurrency limit configurable via RunnerConfig (not env-only)."""

from __future__ import annotations

import pytest

from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.executor.governed import DEFAULT_CONCURRENCY_LIMIT


def test_explicit_limit_overrides_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DRIVER_TOOL_CONCURRENCY", "4")
    ex = GovernedToolExecutor(registry=ToolRegistry(), concurrency_limit=2)
    assert ex._concurrency_limit == 2  # explicit wins over env


def test_none_limit_reads_env(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_DRIVER_TOOL_CONCURRENCY", "5")
    ex = GovernedToolExecutor(registry=ToolRegistry(), concurrency_limit=None)
    assert ex._concurrency_limit == 5


def test_none_limit_falls_back_to_default(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_DRIVER_TOOL_CONCURRENCY", raising=False)
    ex = GovernedToolExecutor(registry=ToolRegistry())
    assert ex._concurrency_limit == DEFAULT_CONCURRENCY_LIMIT


def test_runner_config_carries_limit() -> None:
    assert RunnerConfig().tool_concurrency_limit is None
    assert RunnerConfig(tool_concurrency_limit=3).tool_concurrency_limit == 3


def test_create_agent_accepts_concurrency_limit() -> None:
    # Factory path threads the limit into the governed executor without error.
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        config=RunnerConfig(tool_concurrency_limit=2),
    )
    assert agent.runner.config.tool_concurrency_limit == 2
