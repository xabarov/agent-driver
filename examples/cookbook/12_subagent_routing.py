"""Subagent model routing: cheaper models for cheaper roles.

E6: a declarative ``agent_type -> model`` map on the parent config routes child
runs to different models by role (e.g. a cheap model to explore, a stronger one
to synthesize) without code changes. The resolved model rides ``forced_model``,
so a matching harness profile composes on top.

    python examples/cookbook/12_subagent_routing.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent
from agent_driver.sdk.subagent import SubagentSpec, run_subagent


class _ModelEchoProvider(FakeProvider):
    """Echoes back which model each child run requested."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.models: list[str | None] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.models.append(request.model)
        return await super().complete(request)


async def main() -> None:
    provider = _ModelEchoProvider()
    parent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        config=RunnerConfig(
            subagent_model_routing={
                "explorer": "cheap-explore",
                "synthesizer": "strong-synth",
            }
        ),
    )
    for agent_type in ("explorer", "synthesizer", "unrouted"):
        await run_subagent(parent, SubagentSpec(agent_type=agent_type, prompt="go"))

    print("models requested by role:", provider.models)
    # explorer -> cheap-explore, synthesizer -> strong-synth, unrouted -> None


if __name__ == "__main__":
    asyncio.run(main())
