"""Tests for batch trajectory generation."""

from __future__ import annotations

import pytest

from agent_driver.batch import (
    BatchItem,
    BatchReport,
    BatchRunner,
    InMemoryTrajectoryStore,
    JsonlTrajectoryStore,
    Trajectory,
    items_from_prompts,
)
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def _agent(answer: str = "ok"):
    return create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )


@pytest.mark.asyncio
async def test_runs_all_items_and_aggregates() -> None:
    runner = BatchRunner(_agent("done"), concurrency=3)
    report = await runner.run(items_from_prompts(["a", "b", "c"]))
    assert report.total == 3
    assert report.completed == 3
    assert report.by_status["completed"] == 3
    assert report.total_tokens > 0


@pytest.mark.asyncio
async def test_trajectories_persisted_to_store() -> None:
    store = InMemoryTrajectoryStore()
    runner = BatchRunner(_agent(), concurrency=2)
    await runner.run(items_from_prompts(["p1", "p2"]), store=store)
    assert store.item_ids() == {"item_0", "item_1"}
    assert all(t.answer == "ok" for t in store.trajectories())


@pytest.mark.asyncio
async def test_error_item_is_isolated() -> None:
    class _BoomProvider(FakeProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            raise RuntimeError("provider down")

    agent = create_agent(provider=_BoomProvider(), tools=ToolSet.only())
    report = await BatchRunner(agent).run(items_from_prompts(["x", "y"]))
    assert report.total == 2
    assert report.failed == 2
    assert report.by_status.get("error") == 2


@pytest.mark.asyncio
async def test_resume_skips_completed_items(tmp_path) -> None:
    path = str(tmp_path / "trajectories.jsonl")
    items = items_from_prompts(["one", "two", "three"])

    store = JsonlTrajectoryStore(path=path)
    first = await BatchRunner(_agent()).run(items, store=store)
    assert first.total == 3 and first.skipped_resumed == 0

    # Re-run with a fresh store over the same file: everything is skipped.
    reopened = JsonlTrajectoryStore(path=path)
    second = await BatchRunner(_agent()).run(items, store=reopened, resume=True)
    assert second.total == 0
    assert second.skipped_resumed == 3


class _OneToolThenAnswer(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="final")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="t",
                model="m",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search", args={"query": "x"}
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


@pytest.mark.asyncio
async def test_tool_usage_aggregated() -> None:
    agent = create_agent(
        provider=_OneToolThenAnswer(), tools=ToolSet.only("web_search")
    )
    report = await BatchRunner(agent, concurrency=1).run(items_from_prompts(["go"]))
    assert report.tool_usage.get("web_search", 0) >= 1


def test_report_aggregation_pure() -> None:
    trajectories = [
        Trajectory(
            item_id="a",
            run_id="r1",
            status="completed",
            tool_calls=[{"tool_name": "bash", "status": "completed"}],
            usage={"total_tokens": 10},
        ),
        Trajectory(item_id="b", run_id="r2", status="error", error="boom"),
    ]
    report = BatchReport.from_trajectories(trajectories, skipped_resumed=2)
    assert report.total == 2
    assert report.completed == 1
    assert report.failed == 1
    assert report.tool_usage == {"bash": 1}
    assert report.total_tokens == 10
    assert report.skipped_resumed == 2


def test_batch_item_metadata_must_be_json() -> None:
    BatchItem(item_id="ok", input="hi", metadata={"k": "v"})
    with pytest.raises(ValueError):
        BatchItem(item_id="bad", input="hi", metadata={"k": object()})
