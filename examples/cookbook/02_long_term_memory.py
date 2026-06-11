"""Long-term memory: a fact stored in one run is recalled in a later one.

Attach a ``MemoryProvider`` to the agent; it recalls at run start (injected
into the system prompt) and persists the finished turn. Here a second agent
over the SAME store recalls what the first one was told.

    python examples/cookbook/02_long_term_memory.py
"""

from __future__ import annotations

import asyncio
import tempfile
from pathlib import Path

from agent_driver.llm import FakeProvider
from agent_driver.memory import SqliteMemoryStore, StoreBackedMemoryProvider
from agent_driver.sdk import ToolSet, create_agent


async def main() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        db = str(Path(tmp) / "memory.sqlite3")

        writer_store = SqliteMemoryStore(path=db)
        writer = create_agent(
            provider=FakeProvider(response_text="Noted."),
            tools=ToolSet.only(),
            memory_provider=StoreBackedMemoryProvider(writer_store),
        )
        await writer.session("user-1").send(
            "Remember: the deploy target is eu-west-3.", run_id="m1"
        )
        writer_store.close()

        # A fresh agent + fresh store over the same file — empty transcript,
        # so any recall can only come from durable long-term memory.
        reader_store = SqliteMemoryStore(path=db)
        reader = create_agent(
            provider=FakeProvider(response_text="It is eu-west-3."),
            tools=ToolSet.only(),
            memory_provider=StoreBackedMemoryProvider(reader_store),
        )
        recalled = await reader.session("user-1").send(
            "Where do we deploy?", run_id="m2"
        )
        rows = reader_store.list_for_session("user-1")
        reader_store.close()
        print("answer:", recalled.answer)
        print("recalled facts:", [r.text for r in rows])


if __name__ == "__main__":
    asyncio.run(main())
