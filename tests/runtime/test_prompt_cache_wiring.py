"""D5: RunnerConfig.enable_prompt_cache reaches the provider's LlmRequest."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


class _CacheFlagProvider(FakeProvider):
    """Records enable_prompt_cache from each request it receives."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.flags: list[bool] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.flags.append(request.enable_prompt_cache)
        return await super().complete(request)


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hi",
        run_id=run_id,
        agent_id="a",
        thread_id="t",
        graph_preset="single_react",
    )


@pytest.mark.asyncio
async def test_prompt_cache_disabled_by_default() -> None:
    provider = _CacheFlagProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    await agent.run(_run_input("r1"))
    assert provider.flags and all(flag is False for flag in provider.flags)


@pytest.mark.asyncio
async def test_prompt_cache_flag_flows_to_request() -> None:
    provider = _CacheFlagProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        config=RunnerConfig(enable_prompt_cache=True),
    )
    await agent.run(_run_input("r2"))
    assert provider.flags and all(flag is True for flag in provider.flags)
