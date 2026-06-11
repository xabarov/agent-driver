"""Eval compare: baseline-vs-treatment over a task suite, N runs, median deltas.

T0 testing infrastructure: run the same suite through two configurations that
differ in one harness axis, N times each, and report median + interval deltas
(success / cost / latency / tokens). Here both sides use the fake provider so it
runs offline; live runs use the open-weight OpenRouter preset (see the testing
plan). The CLI wrapper is ``agent-driver eval compare``.

    python examples/cookbook/13_eval_compare.py
"""

from __future__ import annotations

import asyncio

from agent_driver.batch import BatchRunner, items_from_prompts
from agent_driver.evals import render_comparison, run_comparison
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def _agent(answer: str):
    return create_agent(
        provider=FakeProvider(response_text=answer), tools=ToolSet.only()
    )


async def main() -> None:
    items = items_from_prompts(["summarize A", "summarize B", "summarize C"])
    report = await run_comparison(
        BatchRunner(_agent("baseline"), concurrency=3),
        BatchRunner(_agent("treatment"), concurrency=3),
        items,
        repeats=5,  # N-run reliability: report the median, never best-of-N
        baseline_label="prompt_cache_off",
        treatment_label="prompt_cache_on",
    )
    print(render_comparison(report))


if __name__ == "__main__":
    asyncio.run(main())
