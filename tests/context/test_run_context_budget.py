"""Public typed run-context budget and legacy adoption tests."""

from __future__ import annotations

import pytest

from agent_driver.context import resolve_run_context_budget
from agent_driver.contracts import (
    AgentRunInput,
    ContextBudgetDefaults,
    RunContextBudget,
)
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="done")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


def _defaults() -> ContextBudgetDefaults:
    return ContextBudgetDefaults(
        max_chars=6000,
        max_messages=24,
        max_observations=24,
        protect_recent_messages=4,
        preserve_recent_observations=6,
        max_observation_preview_chars=180,
        context_window_estimate=12_000,
        warning_threshold=4200,
        compact_threshold=9000,
        blocking_threshold=11_040,
        output_token_reserve=1500,
        max_compaction_chars=4000,
    )


def _run_input(**updates: object) -> AgentRunInput:
    values: dict[str, object] = {
        "input": "write the report",
        "agent_id": "agent",
        "graph_preset": "single_react",
    }
    values.update(updates)
    return AgentRunInput.model_validate(values)


def test_typed_budget_resolves_full_input_output_and_compaction_window() -> None:
    resolved = resolve_run_context_budget(
        _run_input(
            context_budget=RunContextBudget(
                input_tokens=180_000,
                output_tokens=30_000,
            )
        ),
        _defaults(),
    )

    assert resolved.source == "run_input.context_budget"
    assert resolved.max_chars == 720_000
    assert resolved.max_messages == 360
    assert resolved.max_observations == 360
    assert resolved.protect_recent_messages == 60
    assert resolved.preserve_recent_observations == 90
    assert resolved.max_observation_preview_chars == 2700
    assert resolved.context_window_estimate == 210_000
    assert resolved.max_compaction_chars == 60_000
    assert resolved.audit == {
        "strategy": "typed_run_budget",
        "source": "run_input.context_budget",
        "legacy": False,
        "input_tokens": 180_000,
        "output_tokens": 30_000,
        "max_compaction_chars": 60_000,
    }


def test_typed_semantic_caps_override_scaled_defaults() -> None:
    resolved = resolve_run_context_budget(
        _run_input(
            context_budget=RunContextBudget(
                input_tokens=180_000,
                output_tokens=30_000,
                max_messages=100,
                max_observations=80,
                protect_recent_messages=20,
                preserve_recent_observations=30,
                max_observation_preview_chars=900,
                max_compaction_chars=50_000,
            )
        ),
        _defaults(),
    )
    assert resolved.max_messages == 100
    assert resolved.max_observations == 80
    assert resolved.protect_recent_messages == 20
    assert resolved.preserve_recent_observations == 30
    assert resolved.max_observation_preview_chars == 900
    assert resolved.max_compaction_chars == 50_000


def test_legacy_mapping_is_supported_but_typed_field_wins() -> None:
    resolved = resolve_run_context_budget(
        _run_input(
            context_budget=RunContextBudget(input_tokens=64_000, output_tokens=8000),
            app_metadata={
                "context_budget": {"input_tokens": 1000, "output_tokens": 10}
            },
        ),
        _defaults(),
    )
    assert resolved.source == "run_input.context_budget"
    assert resolved.input_tokens == 64_000


def test_invalid_legacy_mapping_falls_back_without_raw_values_in_audit() -> None:
    resolved = resolve_run_context_budget(
        _run_input(
            app_metadata={
                "context_budget": {
                    "input_tokens": "180000",
                    "output_tokens": 30_000,
                }
            }
        ),
        _defaults(),
    )
    assert resolved.source == "runner_config"
    assert resolved.max_chars == 6000
    assert resolved.audit == {
        "strategy": "runner_defaults",
        "legacy_context_budget_rejected": True,
    }


@pytest.mark.asyncio
async def test_typed_output_budget_reaches_provider_and_output_audit() -> None:
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())

    output = await agent.run(
        _run_input(
            run_id="run_typed_context_budget",
            context_budget=RunContextBudget(
                input_tokens=180_000,
                output_tokens=30_000,
            ),
        )
    )

    assert provider.requests
    assert provider.requests[0].max_tokens == 30_000
    assert output.metadata["effective_context_budget"]["max_chars"] == 720_000
    # Phase-1b: the default compaction budget now derives from the resolved window
    # (BUG-5), so the scaled typed-path cap rises to the contract ceiling
    # MAX_RUN_COMPACTION_CHARS (262144) instead of the old sliver. Lifting that
    # contract-level ceiling to a window fraction is a documented follow-up (BUG-1
    # on the typed path).
    assert (
        output.metadata["effective_context_budget"]["max_compaction_chars"]
        == 262_144
    )
    assert output.metadata["provider_max_tokens_source"] == (
        "run_input.context_budget.output_tokens"
    )
