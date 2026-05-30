# agent-driver

[English](README.md) | [Русский](README.ru.md)

`agent-driver` is a domain-neutral Python runtime for building agentic chat
applications with durable execution, tool governance, and reproducible run
contracts.

Current package version: `0.1.0`

## What is new in this iteration

- Runtime storage split and factory helpers for `memory` / `sqlite` / `postgres`
- Tool-surface selection via `ToolSet` and built-in packs
- Governed tool execution pipeline with policy and output budget handling
- Context compaction and session memory extraction building blocks
- Evaluation and replay entry points for deterministic regression checks
- Code-agent profile primitives and restricted execution contracts

## Key capabilities

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
from agent_driver.llm import FakeProvider
from agent_driver.runtime import (
    RunnerConfig,
    SingleAgentRunner,
    create_runtime_store_bundle,
    preflight_runtime_store,
    runtime_store_config_from_env,
)

cfg = runtime_store_config_from_env()
ready = preflight_runtime_store(cfg)
if not ready.healthy:
    raise RuntimeError(f"runtime store not ready: {ready.reason}")

bundle = create_runtime_store_bundle(cfg)
runner = SingleAgentRunner(
    provider=FakeProvider(),
    checkpoint_store=bundle.checkpoint_store,
    event_log=bundle.event_log,
    config=RunnerConfig(),
)
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

- Main docs index: `docs/README.md`
- Runtime overview: `docs/runtime.md`
- Planning and control: `docs/planning-and-control.md`
- Chat demo: `docs/chat-demo.md`
- Testing: `docs/testing.md`
- Built-in tools overview: `docs/builtin-tools.md`
- Roadmap: `docs/roadmap.md`
- OpenClaude improvement plan: `docs/openclaude-improvement-plan-2026-05-29.md`

## Project status

The repository is actively evolving around the runtime/tooling contract surface
summarized in `docs/roadmap.md`. Public API is still early and may change
between minor iterations.
