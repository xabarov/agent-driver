# Agent Driver Docs

This directory captures the initial architecture analysis for `agent-driver`: a domain-neutral Python LangGraph engine for building agentic chat applications.

## Start Here

- [Agent Driver analysis and extraction plan](agent-driver-analysis-2026-05-18.md) — main overview, boundaries, package shape, and MVP sequence.

## Deep Dives

- [Phase 0 contracts spec](specs/phase-0-contracts.md)
- [Durable runtime, checkpointing, and worker execution](architecture/durable-runtime.md)
- [Human review, interrupts, and guardrails](architecture/hitl-and-guardrails.md)
- [Observability, evaluation, and regression harness](architecture/evaluation-and-observability.md)
- [Context engineering, tools, and MCP integration](architecture/context-tools-and-mcp.md)
- [Implementation roadmap](roadmap.md)

## Development Commands

- `.venv/bin/isort agent_driver tests`
- `.venv/bin/black agent_driver tests`
- `.venv/bin/pylint agent_driver tests`
- `.venv/bin/python -m pytest tests`
- `.venv/bin/python -m pytest tests/runtime/test_runtime_skeleton.py`

## Runtime Store Integration

- Preferred integration path for apps:
  - build store config from env: `runtime_store_config_from_env()`;
  - run readiness check: `preflight_runtime_store(config)`;
  - create store pair once: `create_runtime_store_bundle(config)`;
  - inject into `SingleAgentRunner`.
- Canonical env keys:
  - `AGENT_DRIVER_RUNTIME_STORE_KIND=memory|sqlite|postgres`
  - `AGENT_DRIVER_SQLITE_PATH`
  - `AGENT_DRIVER_POSTGRES_DSN`
  - `AGENT_DRIVER_POSTGRES_SCHEMA` (default: `public`)
  - `AGENT_DRIVER_POSTGRES_AUTO_CREATE_SCHEMA=1|0`

Example:

```python
from agent_driver.runtime import (
    RunnerConfig,
    SingleAgentRunner,
    create_runtime_store_bundle,
    preflight_runtime_store,
    runtime_store_config_from_env,
)
from agent_driver.llm.fake import FakeProvider

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

## Optional Live Checks

- `AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests`
- `AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests/llm/test_live_providers.py`
- `AGENT_DRIVER_RUN_POSTGRES_TESTS=1 AGENT_DRIVER_POSTGRES_DSN=postgresql://... .venv/bin/python -m pytest -m live tests/runtime/test_postgres_store_live.py`
- If `.env` exists in repository root, live tests auto-load it (without printing secret values).
- Optional env vars for live adapters:
  - `AGENT_DRIVER_OPENAI_BASE_URL`, `AGENT_DRIVER_OPENAI_MODEL`, `AGENT_DRIVER_OPENAI_API_KEY`
  - `AGENT_DRIVER_OLLAMA_BASE_URL`, `AGENT_DRIVER_OLLAMA_MODEL`
  - `AGENT_DRIVER_POSTGRES_DSN` (for opt-in PostgreSQL runtime store checks)
- Legacy aliases from `.env.template` are also supported:
  - `OPENROUTER_BASE_URL`, `OPENROUTER_MODEL`, `OPENROUTER_API_KEY`
  - `OLLAMA_BASE_URL`, `OLLAMA_MODEL`

## Optional Extras

- Install PostgreSQL backend support when needed:
  - `.venv/bin/pip install -e .[postgres]`

## External References

- LangChain: [Building LangGraph: Designing an Agent Runtime from First Principles](https://blog.langchain.com/building-langgraph)
- LangGraph docs: [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- LangGraph docs: [Durable execution](https://docs.langchain.com/oss/python/langgraph/durable-execution)
- LangGraph docs: [Human-in-the-loop / interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- LangGraph docs: [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- OpenAI: [Agents SDK guide](https://developers.openai.com/api/docs/guides/agents)
- OpenAI: [Guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
- Anthropic: [Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- Anthropic: [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- Anthropic: [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- Langfuse: [AI agent observability with Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- Langfuse: [Agent evaluation](https://langfuse.com/guides/cookbook/example_pydantic_ai_mcp_agent_evaluation)
- CoSAI: [Model Context Protocol security](https://github.com/cosai-oasis/ws4-secure-design-agentic-systems/blob/main/model-context-protocol-security.md)
