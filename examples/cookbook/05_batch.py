"""Batch: generate trajectories for a list of prompts and aggregate stats.

python examples/cookbook/05_batch.py
"""

from __future__ import annotations

import asyncio

from agent_driver.batch import (
    BatchRunner,
    InMemoryTrajectoryStore,
    compress_trajectories,
    items_from_prompts,
)
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


async def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="answer"), tools=ToolSet.only()
    )
    store = InMemoryTrajectoryStore()
    runner = BatchRunner(agent, concurrency=4)

    report = await runner.run(
        items_from_prompts(["summarize A", "summarize B", "summarize C"]),
        store=store,
    )
    print("total:", report.total, "completed:", report.completed)
    print("by_status:", report.by_status)
    print("total_tokens:", report.total_tokens)
    print("recorded:", sorted(store.item_ids()))

    # Compress the recorded trajectories to a per-example token budget for a
    # training dataset: keep the first + last turn, elide the middle.
    compressed = compress_trajectories(store.trajectories(), max_tokens=256)
    print(
        "compressed:",
        [
            t.metadata.get("compression", {}).get("final_tokens", "—")
            for t in compressed
        ],
    )


if __name__ == "__main__":
    asyncio.run(main())
