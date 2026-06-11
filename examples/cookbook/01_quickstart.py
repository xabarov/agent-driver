"""Quickstart: build an agent and run a one-shot query (offline).

python examples/cookbook/01_quickstart.py
"""

from __future__ import annotations

import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent, summarize_output


async def main() -> str:
    agent = create_agent(
        provider=FakeProvider(response_text="Hello from agent-driver."),
        tools=ToolSet.only(),
    )
    output = await agent.query("Say hello", run_id="demo_quickstart")
    print("answer:", output.answer)
    print("verdict:", summarize_output(output).verdict)
    return output.answer or ""


if __name__ == "__main__":
    asyncio.run(main())
