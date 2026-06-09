# agent-driver

[English](README.md) | [Русский](README.ru.md)

`agent-driver` is a domain-neutral Python runtime for building agentic chat
applications with durable execution, tool governance, and reproducible run
contracts.

Current package version: `0.1.0`

## What is new in this iteration

- SDK entrypoints: `create_agent`, `query`, `Session`, `RunHandle`, `RunStream`
- Typed provider errors with request IDs when providers expose them
- SDK trace summaries, support bundles, and context-pressure diagnostics
- Tool-surface selection via `ToolSet`, built-in packs, and `tool(...)`
- Durable runtime storage, governed tool execution, context compaction, evals

## Key capabilities

- **SDK facade**: one-shot queries, sessions, streaming helpers, resume helpers,
  custom tools, trace summaries, and support bundles
- **Durable runtime**: checkpoint + event-log abstractions with in-memory, SQLite,
  and PostgreSQL backends
- **Tool governance**: registry, manifests, risk/side-effect policy, guardrails,
  and deterministic prompt docs
- **Built-in tool packs**: filesystem, shell, web, planning, tasking, and MCP
  adapters
- **Human-in-the-loop primitives**: structured question and planning/task update
  tools
- **Observability and evals**: trace export, replay projections, dataset-based
  comparisons

## Requirements

- Python `>=3.11`

## Installation

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e .
```

Optional extras:

```bash
pip install -e .[dev]
pip install -e .[cli]
pip install -e .[postgres]
```

## Quick start

```python
import asyncio

from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent, summarize_output


async def main() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="Hello from agent-driver."),
        tools=ToolSet.only(),
    )
    output = await agent.query("Say hello", run_id="demo_run")
    print(output.answer)
    print(summarize_output(output).verdict)


asyncio.run(main())
```

Session and streaming helpers use the same facade:

```python
session = agent.session("user_123")
stream = session.stream("Draft a concise status update")

async for delta in stream.text_deltas():
    print(delta, end="")
```

## Development

```bash
.venv/bin/isort agent_driver tests
.venv/bin/black agent_driver tests
.venv/bin/pylint agent_driver tests
.venv/bin/python -m pytest tests
```

Optional live checks:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests
```

## Documentation map

- Cookbook (offline runnable examples): `examples/cookbook/`
- Extending agent-driver: `docs/extending.md`
- Main docs index: `docs/README.md`
- SDK overview: `docs/sdk.md`
- Sessions: `docs/sdk-sessions.md`
- Tools: `docs/sdk-tools.md`
- Streaming: `docs/sdk-streaming.md`
- Errors: `docs/sdk-errors.md`
- Runtime overview: `docs/runtime.md`
- Planning and control: `docs/planning-and-control.md`
- Chat demo: `docs/chat-demo.md`
- Testing: `docs/testing.md`
- Built-in tools overview: `docs/builtin-tools.md`
- Roadmap: `docs/roadmap.md`

## Project status

The repository is actively evolving around the runtime/tooling contract surface
summarized in `docs/roadmap.md`. Public API is still early and may change
between minor iterations.
