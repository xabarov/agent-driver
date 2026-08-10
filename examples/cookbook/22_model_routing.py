"""Model routing: pick the model per run — explicitly or by difficulty (offline).

R-track. Two ways to route, both from the SDK surface (no raw RunnerConfig):

  * S1 sugar — pass ``model_role`` / ``reasoning_effort`` straight to ``query``
    (or ``run_text`` / ``Session.send`` / ``.stream`` / ``.start``). The role
    resolves through the agent's ``model_role_map`` and rides ``request.model``;
    the effort tier rides ``request.reasoning``.
  * S2 auto-routing — hand ``create_agent`` a ``model_router`` (here a
    ``HeuristicDifficultyRouter``) and it picks ``simple`` vs ``strong`` per turn
    from the request itself — a short ask stays cheap, a complex one goes strong.

    python examples/cookbook/22_model_routing.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider, HeuristicDifficultyRouter
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.sdk import ToolSet, create_agent

_ROLE_MODELS = {"simple": "cheap-model", "strong": "strong-model"}


class _RequestRecorder(FakeProvider):
    """Records the model + reasoning envelope each run requested."""

    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.calls: list[tuple[str | None, object]] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.calls.append((request.model, request.reasoning))
        return await super().complete(request)


async def main() -> list[tuple[str | None, object]]:
    # --- S1: explicit role + effort, no router ------------------------------
    explicit = _RequestRecorder()
    agent = create_agent(
        provider=explicit, tools=ToolSet.only(), model_role_map=_ROLE_MODELS
    )
    await agent.query("2+2?", run_id="r_simple", model_role="simple")
    await agent.query(
        "Design a schema", run_id="r_strong", model_role="strong",
        reasoning_effort="high",
    )
    print("explicit routing (model, reasoning):", explicit.calls)
    # -> [('cheap-model', None), ('strong-model', {reasoning: high...})]

    # --- S2: let a difficulty router choose the role per run ----------------
    routed = _RequestRecorder()
    auto = create_agent(
        provider=routed,
        tools=ToolSet.only(),
        model_role_map=_ROLE_MODELS,
        model_router=HeuristicDifficultyRouter(),
    )
    await auto.query("hi", run_id="r_auto_simple")
    await auto.query(
        "Analyze the trade-offs and design a migration plan.", run_id="r_auto_strong"
    )
    print("auto-routed models:", [model for model, _ in routed.calls])
    # -> ['cheap-model', 'strong-model']  (short -> simple, complex -> strong)
    return routed.calls


if __name__ == "__main__":
    asyncio.run(main())
