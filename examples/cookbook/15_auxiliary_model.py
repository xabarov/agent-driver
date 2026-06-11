"""Auxiliary model routing: run side tasks on a cheaper model.

E1: context compaction is a frequent side task that doesn't need the main
(expensive) model. ``RunnerConfig(auxiliary_provider=..., auxiliary_model=...)``
routes the compaction LLM call to a cheaper provider/model; its spend is tagged
by that model in the cost ledger, separate from the main model. Here a capturing
auxiliary provider records that the compaction call was routed to it.

    python examples/cookbook/15_auxiliary_model.py
"""

from __future__ import annotations

import asyncio

from agent_driver.contracts import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import RunnerConfig
from agent_driver.sdk import ToolSet, create_agent


class _AuxCapturingProvider(FakeProvider):
    """A cheap auxiliary provider that records the compaction calls it handles."""

    def __init__(self) -> None:
        super().__init__(name="aux", response_text="ok")
        self.compaction_models: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        if (request.metadata or {}).get("compaction_mode") == "llm_full":
            self.compaction_models.append(request.model or "")
        return await super().complete(request)


async def main() -> None:
    main_provider = FakeProvider(response_text="final answer")
    aux = _AuxCapturingProvider()
    agent = create_agent(
        provider=main_provider,
        tools=ToolSet.only(),
        config=RunnerConfig(
            # Force LLM compaction to fire on a small budget so the demo triggers.
            enable_compaction=True,
            enable_llm_compaction=True,
            token_compact_threshold=1,
            token_blocking_threshold=2,
            context_window_estimate=100,
            output_token_reserve=1,
            # Route the compaction side task to the cheap auxiliary model.
            auxiliary_provider=aux,
            auxiliary_model="cheap-aux",
        ),
    )
    await agent.run(
        AgentRunInput(
            input="summarize this " * 100,
            run_id="aux-demo",
            agent_id="a",
            thread_id="t",
            graph_preset="single_react",
        )
    )
    print("compaction routed to auxiliary model:", aux.compaction_models[:1] or "none")
    print("main provider answered the run (separate from compaction).")


if __name__ == "__main__":
    asyncio.run(main())
