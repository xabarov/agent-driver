"""E1: auxiliary (cheap) model routing for compaction + cost separation."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)
from agent_driver.runtime.metadata_state import get_cost_runtime_state
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _account_compaction_cost,
)


def test_account_compaction_cost_tags_by_model() -> None:
    """Compaction usage lands in the ledger under its own model name."""
    context = SimpleNamespace(metadata={})
    result = SimpleNamespace(
        input_tokens_estimate=120,
        output_tokens_estimate=30,
        model="cheap-aux",
    )
    _account_compaction_cost(
        context, result, provider=SimpleNamespace(name="openrouter")
    )
    ledger = get_cost_runtime_state(context).ledger()
    assert ledger.total_tokens() == 150
    dump = json.dumps(ledger.model_dump(mode="json"))
    assert "cheap-aux" in dump  # separated by the auxiliary model name


def test_account_compaction_cost_noops_without_tokens() -> None:
    context = SimpleNamespace(metadata={})
    _account_compaction_cost(
        context,
        SimpleNamespace(input_tokens_estimate=0, output_tokens_estimate=0, model="x"),
        provider=SimpleNamespace(name="p"),
    )
    assert get_cost_runtime_state(context).ledger().total_tokens() == 0
    _account_compaction_cost(context, None, provider=SimpleNamespace(name="p"))
    assert get_cost_runtime_state(context).ledger().total_tokens() == 0


class _AuxCapturingProvider(FakeProvider):
    """Records compaction calls routed to it (by the no-tools metadata flag)."""

    def __init__(self) -> None:
        super().__init__(name="aux", response_text="ok")
        self.compaction_models: list[str] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        if (request.metadata or {}).get("compaction_mode") == "llm_full":
            self.compaction_models.append(request.model or "")
        return await super().complete(request)


@pytest.mark.asyncio
async def test_compaction_routed_to_auxiliary_provider() -> None:
    """When configured, the full-compaction call uses the auxiliary provider."""
    main = FakeProvider(response_text="ok")
    aux = _AuxCapturingProvider()
    runner = FakeSingleStepRunner(
        provider=main,
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=InMemoryEventLog(),
        config=RunnerConfig(
            enable_compaction=True,
            enable_llm_compaction=True,
            token_compact_threshold=1,
            token_blocking_threshold=2,
            context_window_estimate=100,
            output_token_reserve=1,
            auxiliary_provider=aux,
            auxiliary_model="cheap-aux",
        ),
    )
    await runner.run(
        AgentRunInput(
            input="hello " * 100,
            run_id="run_aux",
            agent_id="agent-test",
            graph_preset="single_react",
        )
    )
    # The compaction side task went to the auxiliary provider + model.
    assert aux.compaction_models and all(
        m == "cheap-aux" for m in aux.compaction_models
    )
