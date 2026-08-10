"""Self-consistency: sample N times and plurality-vote — across models (offline).

``run_self_consistent`` runs the same request several times and keeps the answer
the samples most agree on — recovering the right answer when it's the plurality
even if not every run. The S4 ``vary_run_input`` hook makes the vote
*model-diverse*, not just seed-diverse: route each sample to a different role/
model/effort and vote across them, so no single model's bias decides the result.

    python examples/cookbook/24_self_consistency.py
"""

from __future__ import annotations

import asyncio

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm import FakeProvider
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.sdk import ToolSet, create_agent, run_self_consistent


class _ModelRecorder(FakeProvider):
    """Answers consistently but records which model each sample ran on."""

    def __init__(self) -> None:
        super().__init__(response_text="42")
        self.models: list[str | None] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.models.append(request.model)
        return await super().complete(request)


async def main() -> float:
    provider = _ModelRecorder()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        model_role_map={"simple": "cheap-model", "strong": "strong-model"},
    )
    run_input = AgentRunInput(
        input="What is the answer?",
        run_id="demo_sc",
        agent_id="agent",
        graph_preset="single_react",
    )

    roles = ("simple", "strong")
    result = await run_self_consistent(
        agent,
        run_input,
        samples=4,
        vary_run_input=lambda ri, i: ri.model_copy(
            update={"model_role": roles[i % len(roles)]}
        ),
    )
    print("consensus:", result.consensus_key, "confidence:", result.confidence)
    print("voted across models:", sorted({m for m in provider.models if m}))
    # consensus '42' at confidence 1.0, voted across cheap-model + strong-model.
    return result.confidence


if __name__ == "__main__":
    asyncio.run(main())
