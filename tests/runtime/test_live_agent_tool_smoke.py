"""Optional live smoke: runner + OpenAI-compatible provider + built-in tools."""

from __future__ import annotations

import os

import pytest

from agent_driver.contracts import AgentRunInput, ToolCall
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry, register_builtin_tools
from tests.live_env import load_local_dotenv_for_live_tests

pytestmark = pytest.mark.live


class _RequestMetadataEchoProvider:
    """Wrap live provider and echo request metadata back into response metadata."""

    def __init__(self, provider: OpenAICompatibleProvider) -> None:
        self._provider = provider

    @property
    def name(self) -> str:
        """Expose wrapped provider name for runtime events."""
        return self._provider.name

    async def healthcheck(self):
        """Delegate health probe to wrapped provider."""
        return await self._provider.healthcheck()

    async def complete(self, request: LlmRequest) -> LlmResponse:
        """Delegate completion and preserve request metadata for tool planning."""
        response = await self._provider.complete(request)
        return response.model_copy(
            update={"metadata": {**response.metadata, **request.metadata}}
        )

    async def stream(self, request: LlmRequest):
        """Delegate streaming without metadata mutation."""
        async for event in self._provider.stream(request):
            yield event


def _live_enabled() -> bool:
    return os.getenv("AGENT_DRIVER_RUN_LIVE_TESTS", "").strip() == "1"


def _env(name: str, fallback: str | None = None) -> str | None:
    """Resolve env var from AGENT_DRIVER_* or legacy OpenRouter names."""
    value = os.getenv(name)
    if value:
        return value
    legacy_map = {
        "AGENT_DRIVER_OPENAI_BASE_URL": "OPENROUTER_BASE_URL",
        "AGENT_DRIVER_OPENAI_API_KEY": "OPENROUTER_API_KEY",
        "AGENT_DRIVER_OPENAI_MODEL": "OPENROUTER_MODEL",
    }
    legacy = legacy_map.get(name)
    if legacy:
        legacy_value = os.getenv(legacy)
        if legacy_value:
            return legacy_value
    return fallback


load_local_dotenv_for_live_tests()


@pytest.mark.asyncio
@pytest.mark.skipif(
    not _live_enabled(), reason="live tests require AGENT_DRIVER_RUN_LIVE_TESTS=1"
)
async def test_live_agent_run_with_governed_builtin_tool_call() -> None:
    """Run one live LLM call plus one deterministic built-in tool stage."""
    base_url = _env("AGENT_DRIVER_OPENAI_BASE_URL")
    model = _env("AGENT_DRIVER_OPENAI_MODEL")
    api_key = _env("AGENT_DRIVER_OPENAI_API_KEY")
    if not base_url or not model:
        pytest.skip(
            "AGENT_DRIVER_OPENAI_BASE_URL and AGENT_DRIVER_OPENAI_MODEL are required"
        )
    provider = _RequestMetadataEchoProvider(
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
    runner = FakeSingleStepRunner(
        provider=provider,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            tool_executor=wrap_governed_executor(
                GovernedToolExecutor(registry=registry)
            )
        ),
    )
    output = await runner.run(
        AgentRunInput(
            input="Say hello in one short sentence.",
            run_id="run_live_agent_tool_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            args={
                                "query": "agent driver runtime",
                                "mock_results": [
                                    {
                                        "title": "Agent Driver",
                                        "url": "https://example.com",
                                        "snippet": "runtime",
                                    }
                                ],
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    assert output.metadata["tool_results"]
    assert output.metadata["tool_results"][0]["call"]["tool_name"] == "web_search"
