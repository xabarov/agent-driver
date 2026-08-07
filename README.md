# agent-driver

[English](README.md) | [Русский](README.ru.md)

`agent-driver` is a domain-neutral Python runtime for building agentic chat
applications with durable execution, tool governance, and reproducible run
contracts.

**Embedding it?** Start with the supported public API surface and stability
policy: [docs/embedding.md](docs/embedding.md). Runnable recipes:
[examples/cookbook](examples/cookbook/README.md).

Current package version: `0.12.0`

**Release 0.12.0** (backward-compatible MINOR over `0.11.0`) adds bounded,
synthesis-only final-answer revision gates with an optional fail-closed terminal.
Existing lifecycle hooks keep their historical behavior because every new
`RevisionRequest` option defaults off. See [CHANGELOG](CHANGELOG.md) `[0.12.0]`.

## What is new in this iteration

- SDK entrypoints: `create_agent`, `query`, `Session`, `RunHandle`, `RunStream`
- Self-consistency runs with plurality voting via `run_self_consistent`
- Typed provider errors, request IDs, route profiles, and preflight summaries
- SDK trace summaries, support bundles, context-pressure diagnostics, and
  redaction-safe provider diagnostics
- Tool-surface selection via `ToolSet`, built-in packs, and `tool(...)`
- Deferred-tool priming, soft-budget final-answer grace, governed tool
  execution, context compaction, evals

## Key capabilities

- **SDK facade**: one-shot queries, sessions, streaming helpers, resume helpers,
  custom tools, self-consistency sampling, trace summaries, and support bundles
- **Durable runtime**: checkpoint + event-log abstractions with in-memory, SQLite,
  and PostgreSQL backends, plus bounded step-loop defaults and budget grace
- **Tool governance**: registry, manifests, risk/side-effect policy, guardrails,
  `success_field` failure mapping, and deterministic prompt docs
- **Built-in tool packs**: filesystem, shell, web, planning, tasking, and MCP
  adapters
- **Human-in-the-loop primitives**: structured question and planning/task update
  tools
- **Observability and evals**: trace export, replay projections, dataset-based
  comparisons
- **Provider diagnostics**: OpenAI-compatible route profiles, deterministic
  preflight summaries, request-shape downgrade notes, and single-provider retry

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
cp .env.template .env
set -a && . ./.env && set +a
.venv/bin/python -m pytest -m live tests
```

The template documents the live provider, timeout, runtime-store, Postgres,
server auth, and Python tool variables. Live provider checks require
`AGENT_DRIVER_API_KEY`, `AGENT_DRIVER_BASE_URL`, `AGENT_DRIVER_MODEL`, and
`AGENT_DRIVER_RUN_LIVE_TESTS=1`; Postgres checks additionally require
`AGENT_DRIVER_RUN_POSTGRES_TESTS=1` and `AGENT_DRIVER_POSTGRES_DSN`.

Common make targets:

```bash
make test
make format-check
make lint
make selftest-fake
```

## Documentation map

- Embedding (public API surface + stability): `docs/embedding.md`
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
- Provider/model debugging and route preflight: `docs/provider-model-debugging.md`
- Node contract: `docs/node-contract.md`
- Chat demo: `docs/chat-demo.md`
- Testing: `docs/testing.md`
- Built-in tools overview: `docs/builtin-tools.md`
- Server surfaces: `docs/server.md`, `docs/mcp-http.md`, `docs/acp.md`,
  `docs/a2a.md`
- Roadmap: `docs/roadmap.md`

## Project status

The repository is actively evolving around the runtime/tooling contract surface
summarized in `docs/roadmap.md`. Public API is still early and may change
between minor iterations.
