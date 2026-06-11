"""N1 — cost governance: ledger accumulation, budget enforcement, summary."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.observability.cost_ledger import (
    CostLedger,
    Pricing,
    format_cost_summary,
    register_pricing,
)
from agent_driver.runtime.metadata_state import get_cost_runtime_state
from agent_driver.sdk import ToolSet, create_agent


def test_cost_runtime_state_accumulates() -> None:
    ctx = SimpleNamespace(metadata={})
    state = get_cost_runtime_state(ctx)
    state.accumulate(
        UsageSummary(input_tokens=1000, output_tokens=1000, model_name="gpt-4o-mini")
    )
    state.accumulate(
        UsageSummary(input_tokens=1000, output_tokens=0, model_name="gpt-4o-mini")
    )
    # gpt-4o-mini: in 0.15/M, out 0.6/M -> (2000*0.15 + 1000*0.6)/1e6 = 0.0009
    assert round(state.total_cost_usd(), 6) == 0.0009
    assert (
        ctx.metadata["cost_ledger"]["per_model"]["gpt-4o-mini"]["input_tokens"] == 2000
    )


def test_cache_hit_rate_and_summary() -> None:
    ledger = CostLedger()
    ledger.accumulate(
        UsageSummary(
            input_tokens=200,
            output_tokens=50,
            cache_read_tokens=800,
            model_name="gpt-4o-mini",
        )
    )
    # cache_read / (input + cache_read) = 800 / 1000 = 0.8
    assert ledger.cache_hit_rate() == pytest.approx(0.8)
    summary = format_cost_summary(ledger)
    assert "gpt-4o-mini" in summary
    assert "hit 80%" in summary
    assert format_cost_summary(CostLedger()) == "no model usage recorded"


class _PricedProvider(FakeProvider):
    """Returns a fixed, priced usage so a tiny budget is exceeded in one call."""

    async def complete(self, request) -> LlmResponse:  # noqa: ANN001
        return LlmResponse(
            message=ChatMessage(role="assistant", content="answer"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(
                input_tokens=1000,
                output_tokens=1000,
                total_tokens=2000,
                model_name="budget-test-model",
            ),
            provider="fake",
            model="budget-test-model",
        )


@pytest.fixture(autouse=True)
def _price_test_model() -> None:
    # ~$2 per call (1000 in + 1000 out at $1000/M each).
    register_pricing(
        "budget-test-model",
        Pricing(input_per_million=1000.0, output_per_million=1000.0),
    )


def _run_input(run_id: str, *, budget: float | None) -> AgentRunInput:
    return AgentRunInput(
        input="hi",
        run_id=run_id,
        thread_id="t1",
        agent_id="agent",
        graph_preset="single_react",
        cost_budget_usd=budget,
    )


@pytest.mark.asyncio
async def test_run_fails_fast_when_budget_exceeded() -> None:
    agent = create_agent(provider=_PricedProvider(), tools=ToolSet.only())
    output = await agent.run(_run_input("run_over_budget", budget=0.5))
    assert output.status.value == "failed"
    assert output.terminal_reason.value == "budget_exceeded"


@pytest.mark.asyncio
async def test_run_completes_when_under_budget() -> None:
    agent = create_agent(provider=_PricedProvider(), tools=ToolSet.only())
    output = await agent.run(_run_input("run_under_budget", budget=100.0))
    assert output.status.value == "completed"


@pytest.mark.asyncio
async def test_no_budget_does_not_gate() -> None:
    agent = create_agent(provider=_PricedProvider(), tools=ToolSet.only())
    output = await agent.run(_run_input("run_no_budget", budget=None))
    assert output.status.value == "completed"


def test_negative_budget_rejected() -> None:
    with pytest.raises(ValueError):
        AgentRunInput(
            input="x",
            run_id="r",
            agent_id="a",
            graph_preset="single_react",
            cost_budget_usd=0,
        )
