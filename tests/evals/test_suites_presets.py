"""T0: general task suite + open-weight preset + suite-level cost ceiling."""

from __future__ import annotations

import pytest

from agent_driver.batch import BatchRunner, InMemoryTrajectoryStore, items_from_prompts
from agent_driver.evals import (
    OPENWEIGHT_MODELS,
    general_task_suite,
    openweight_preset,
    openweight_provider_spec,
    suite_categories,
)
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def test_general_suite_is_non_coding_and_tagged() -> None:
    suite = general_task_suite()
    assert len(suite) >= 10
    cats = {item.metadata["category"] for item in suite}
    # Covers the general mechanics, not coding.
    assert {
        "tool_use",
        "dialog",
        "retrieval",
        "summarization",
        "planning",
        "memory",
    } <= cats
    assert set(suite_categories()) == cats
    # Unique ids.
    assert len({item.item_id for item in suite}) == len(suite)


def test_openweight_preset_tiers() -> None:
    for tier in ("small", "mid", "large"):
        preset = openweight_preset(tier)
        assert preset.model == OPENWEIGHT_MODELS[tier]
        assert preset.temperature > 0.0
    with pytest.raises(ValueError):
        openweight_preset("frontier")


def test_openweight_provider_spec_targets_openrouter() -> None:
    spec = openweight_provider_spec("mid", api_key="k")
    assert spec.provider_id == "openrouter"
    assert spec.model == OPENWEIGHT_MODELS["mid"]
    assert spec.api_key == "k"


@pytest.mark.asyncio
async def test_cost_ceiling_skips_remaining_runs() -> None:
    """With a $0 ceiling, every run is recorded as skipped_budget, none query."""
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    store = InMemoryTrajectoryStore()
    runner = BatchRunner(agent, concurrency=1)
    report = await runner.run(
        items_from_prompts(["a", "b", "c"]), store=store, max_total_cost_usd=0.0
    )
    statuses = [t.status for t in store.trajectories()]
    assert statuses == ["skipped_budget"] * 3
    assert report.completed == 0
