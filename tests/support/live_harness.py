"""Shared helpers for optional live OpenRouter/OpenAI-compatible tests."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    register_builtin_tools,
    register_planning_tool,
)
from tests.live_env import load_local_dotenv_for_live_tests

load_local_dotenv_for_live_tests()


class RequestMetadataEchoProvider:
    """Wrap live provider and echo request metadata into response metadata."""

    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        return self._provider.name

    async def healthcheck(self):
        return await self._provider.healthcheck()

    async def complete(self, request: LlmRequest) -> LlmResponse:
        response = await self._provider.complete(request)
        return response.model_copy(
            update={"metadata": {**response.metadata, **request.metadata}}
        )

    async def stream(self, request: LlmRequest):
        async for event in self._provider.stream(request):
            yield event


def live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


def live_env(name: str, fallback: str | None = None) -> str | None:
    value = os.getenv(name)
    if value:
        return value
    return fallback


def require_live_openrouter_config() -> tuple[str, str, str | None]:
    base_url = live_env("AGENT_DRIVER_BASE_URL")
    model = live_env("AGENT_DRIVER_MODEL")
    api_key = live_env("AGENT_DRIVER_API_KEY")
    if not base_url or not model:
        pytest.skip("AGENT_DRIVER_BASE_URL and AGENT_DRIVER_MODEL are required")
    return base_url, model, api_key


def tool_result(output, tool_name: str) -> dict[str, Any]:
    tool_results = output.metadata.get("tool_results", [])
    if not isinstance(tool_results, list):
        return {}
    for row in tool_results:
        if not isinstance(row, dict):
            continue
        call = row.get("call")
        if isinstance(call, dict) and call.get("tool_name") == tool_name:
            return row
    return {}


def notebook_fixture(path: Path) -> None:
    payload = {
        "cells": [
            {
                "cell_type": "code",
                "execution_count": None,
                "metadata": {},
                "outputs": [],
                "source": ["print('old')\n"],
            }
        ],
        "metadata": {},
        "nbformat": 4,
        "nbformat_minor": 5,
    }
    path.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")


def build_live_runner(base_url: str, model: str, api_key: str | None) -> FakeSingleStepRunner:
    provider = RequestMetadataEchoProvider(
        OpenAICompatibleProvider(
            config=OpenAICompatibleProvider.Config(
                name="openai-live",
                base_url=base_url,
                api_key=api_key,
                model=model,
            )
        )
    )
    registry = ToolRegistry()
    register_builtin_tools(registry)
    register_planning_tool(registry)
    return FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )


def assert_live_interrupt_for_tool(output, tool_name: str) -> None:
    assert output.status.value == "paused"
    assert output.interrupt is not None
    assert output.interrupt.reason.value == "approval_required"
    approval_payload = output.metadata.get("approval_payload")
    assert isinstance(approval_payload, dict)
    assert approval_payload.get("tool_name") == tool_name
    assert any(
        item.tool_name == tool_name and item.status.value == "denied"
        for item in output.tool_trace
    )
    envelope = tool_result(output, tool_name)
    assert envelope
    assert envelope["decision"] == "interrupt"


__all__ = [
    "RequestMetadataEchoProvider",
    "assert_live_interrupt_for_tool",
    "build_live_runner",
    "live_enabled",
    "live_env",
    "notebook_fixture",
    "require_live_openrouter_config",
    "tool_result",
]
