"""Runtime wiring for the opt-in JEV compaction pre-pass."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.llm.compaction_prepass import (
    CompactionPrepassResult,
    CompactionUnitDecision,
)
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.rollout import JevRolloutSettings
from agent_driver.runtime.single_agent.context_management.compaction_stage import (
    _apply_jev_compaction_prepass,
)
from agent_driver.runtime.single_agent.types import RunContext, RunnerConfig


class _ArchiveOldExchange:
    async def classify(self, *, active_state, units):
        old = next(unit for unit in units if unit.message_indexes == (1, 2))
        current = next(unit for unit in units if unit.message_indexes == (3,))
        return CompactionPrepassResult(
            decisions=(
                CompactionUnitDecision(
                    unit_id=old.unit_id,
                    message_indexes=old.message_indexes,
                    retention="archive",
                ),
                CompactionUnitDecision(
                    unit_id=current.unit_id,
                    message_indexes=current.message_indexes,
                    retention="protected",
                    protected=True,
                ),
            ),
            archive_indexes=frozenset({1, 2}),
            receipt={
                "schema": "jev-compaction.v1",
                "source": "jev",
                "archived_unit_sha256": ["hash"],
            },
        )


@pytest.mark.asyncio
async def test_prepass_filters_archive_units_and_keeps_live_turn() -> None:
    config = RunnerConfig(
        enable_jev_compaction_prepass=True,
        compaction_prepass=_ArchiveOldExchange(),
    )
    host = SimpleNamespace(_config=config)
    context = RunContext(
        run_input=AgentRunInput(
            input="current request",
            agent_id="agent",
            graph_preset="single_react",
        ),
        identifiers={"run_id": "run", "attempt_id": "attempt"},
    )
    request = LlmRequest(
        messages=[
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="old question"),
            ChatMessage(role="assistant", content="old answer"),
            ChatMessage(role="user", content="current request"),
        ]
    )

    await _apply_jev_compaction_prepass(host, context=context, request=request)

    assert [message.content for message in request.messages] == [
        "system",
        "current request",
    ]
    assert context.metadata["compaction_prepass"]["archived_message_count"] == 2
    assert context.metadata["compaction_prepass"]["chars_archived"] > 0


@pytest.mark.asyncio
async def test_prepass_shadow_keeps_request_unchanged_and_records_proposal() -> None:
    config = RunnerConfig(
        enable_jev_compaction_prepass=True,
        compaction_prepass=_ArchiveOldExchange(),
        jev_rollout=JevRolloutSettings(mode="shadow"),
    )
    host = SimpleNamespace(_config=config)
    context = RunContext(
        run_input=AgentRunInput(
            input="current request",
            agent_id="agent",
            graph_preset="single_react",
        ),
        identifiers={"run_id": "run", "attempt_id": "attempt"},
    )
    request = LlmRequest(
        messages=[
            ChatMessage(role="system", content="system"),
            ChatMessage(role="user", content="old question"),
            ChatMessage(role="assistant", content="old answer"),
            ChatMessage(role="user", content="current request"),
        ]
    )

    await _apply_jev_compaction_prepass(host, context=context, request=request)

    assert [message.content for message in request.messages] == [
        "system",
        "old question",
        "old answer",
        "current request",
    ]
    receipt = context.metadata["compaction_prepass"]
    assert receipt["rollout_mode"] == "shadow"
    assert receipt["applied"] is False
    assert receipt["proposed_archived_message_count"] == 2
