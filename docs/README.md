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
- [Built-in tools overview](builtin-tools.md)
- [SDK + ToolSet examples](examples/sdk-toolset-examples.md)
- [Multi-agent orchestration and parallel subagents](architecture/multi-agent-orchestration.md)
- [Smolagents lessons for agent profiles, prompts, and tools](architecture/smolagents-lessons.md)
- [Multi-mode prompt assembly](patterns/multi-mode-prompts.md) — when an agent has ask / plan / code modes, substitute the behaviour block instead of prepending a "mode header"; reference impl in `excel_ai`
- [Testing and live trace policy](architecture/testing-and-live-trace-policy.md)
- [Test plan and coverage matrix](architecture/test-plan-and-matrix.md)
- [Next-stage follow-up tracks](architecture/next-stage-followups.md)
- [Package layout and shim policy](architecture/package-layout.md)
- [Custom CLI roadmap (OpenClaude-informed)](architecture/custom-cli-roadmap.md)
- [Implementation roadmap](roadmap.md)
- [Refactor backlog and quality rules](refactor/README.md) — structure status, pylint policy, package split priorities

## Development Commands

- `.venv/bin/isort agent_driver tests`
- `.venv/bin/black agent_driver tests`
- `.venv/bin/pylint agent_driver tests`
- `.venv/bin/python -m pytest tests`
- `.venv/bin/python -m pytest tests/runtime/test_runtime_runner_core.py tests/runtime/test_runtime_stores.py`

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
from agent_driver.llm.providers_impl.fake import FakeProvider
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

## Tool Governance Integration (Phase 3 first cut)

- Register deterministic tools in `ToolRegistry` with `ToolManifest`.
- Build governed executor and adapt it for runtime with `wrap_governed_executor(...)`.
- Pass adapted executor through `RunnerConfig(tool_executor=...)`.
- Encode planned calls in LLM response metadata key `planned_tool_calls` for tests/local demos.

Example:

```python
from agent_driver.contracts import AgentRunInput, ToolManifest
from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
    wrap_governed_executor,
)
from agent_driver.tools import GovernedToolExecutor, GuardrailPipeline, ToolRegistry

registry = ToolRegistry()

async def lookup_tool(args: dict[str, object]) -> dict[str, object]:
    return {"summary": f"result for {args.get('query', '')}"}

registry.register(
    ToolManifest(
        name="lookup",
        description="Read-only lookup tool",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        output_char_budget=500,
    ),
    lookup_tool,
)
governed = GovernedToolExecutor(registry=registry, guardrails=GuardrailPipeline())
runner = FakeSingleStepRunner(
    provider=FakeProvider(),
    checkpoint_store=InMemoryCheckpointStore(),
    event_log=InMemoryEventLog(),
    config=RunnerConfig(tool_executor=wrap_governed_executor(governed)),
)
_ = AgentRunInput(
    input="find document",
    agent_id="agent.default",
    graph_preset="single_react",
)
```

## Observability Entry Points (Phase 5)

- Build deterministic trace payload from one run output:
  - `build_trace_export(output)`
- Export to local/no-op sinks:
  - `NoOpTraceExporter`
  - `LocalTraceExporter`

Example:

```python
from agent_driver.observability import (
    LocalTraceExporter,
    NoOpTraceExporter,
    build_trace_export,
)

trace_payload = build_trace_export(run_output)
_ = NoOpTraceExporter().export(trace_payload)

local_exporter = LocalTraceExporter()
sink_result = local_exporter.export(trace_payload)
stored = local_exporter.get(trace_payload.trace_id)
```

## Evaluation Harness Entry Points (Phase 5)

- Deterministic evaluators:
  - `default_evaluators()`
  - `evaluate_event_schema(...)`
  - `evaluate_terminal_state(...)`
  - `evaluate_tool_policy(...)`
  - `evaluate_checkpoint_replay(...)`
  - `evaluate_cost_latency_budget(...)`
- Dataset runner and report compare:
  - `run_dataset(...)`
  - `compare_reports(...)`
- Replay/devtools projections:
  - `render_full_debug_view(...)`
  - `render_succinct_view(...)`
  - `render_cli_replay(...)`
  - `build_support_bundle(...)`
  - `build_runtime_support_bundle(...)`
  - `build_persisted_support_bundle(...)`

Example:

```python
from agent_driver.evals import (
    BudgetLimits,
    DatasetCase,
    compare_reports,
    run_dataset,
)

budget = BudgetLimits(max_total_tokens=20_000, max_latency_ms=30_000)
report = await run_dataset(
    cases=[DatasetCase(...)],
    run_executor=run_case,
    candidate_id="local-candidate",
    limits=budget,
)
comparison = compare_reports(baseline=baseline_report, candidate=report)
```

## CodeAgent Entry Points (Phase 7)

- Code-agent contracts and limits:
  - `CodeAgentAction`
  - `CodeAgentLimits`
  - `CodeAgentExecutionResult`
- Sandboxed executor:
  - `FakeRestrictedCodeExecutor`
  - `validate_code_action(...)`
  - `serialize_payload(...)` / `deserialize_payload(...)`
- Callable tool surface:
  - `build_callable_tool_surface(...)`
  - `render_callable_tool_docs(...)`
- Runtime integration:
  - set `agent_profile=AgentProfile.CODE_AGENT` in `AgentRunInput`
  - pass code action via `tool_policy.metadata["code_action"]`
  - use `RunnerConfig(code_executor=..., tool_registry=..., authorized_imports=(...))`

Example:

```python
from agent_driver.contracts import AgentProfile, AgentRunInput
from agent_driver.code_agent import FakeRestrictedCodeExecutor
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime import (
    FakeSingleStepRunner,
    InMemoryCheckpointStore,
    InMemoryEventLog,
    RunnerConfig,
)

runner = FakeSingleStepRunner(
    provider=FakeProvider(response_text="ignored"),
    checkpoint_store=InMemoryCheckpointStore(),
    event_log=InMemoryEventLog(),
    config=RunnerConfig(
        code_executor=FakeRestrictedCodeExecutor(),
        authorized_imports=("math",),
    ),
)
output = await runner.run(
    AgentRunInput(
        input="compute",
        run_id="run_code",
        agent_id="agent.default",
        graph_preset="single_react",
        agent_profile=AgentProfile.CODE_AGENT,
        tool_policy={"metadata": {"code_action": "final_answer(2 + 2)"}},
    )
)
```

## Optional Live Checks

- `AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests`
- `AGENT_DRIVER_RUN_LIVE_TESTS=1 .venv/bin/python -m pytest -m live tests/llm/test_live_providers.py`
- `AGENT_DRIVER_RUN_POSTGRES_TESTS=1 AGENT_DRIVER_POSTGRES_DSN=postgresql://... .venv/bin/python -m pytest -m live tests/runtime/test_postgres_store_live.py`
- If `.env` exists in repository root, live tests auto-load it (without printing secret values).
- Optional env vars for live adapters:
  - `AGENT_DRIVER_PROVIDER` (`openrouter` | `vllm` | `ollama`)
  - `AGENT_DRIVER_BASE_URL`, `AGENT_DRIVER_MODEL`, `AGENT_DRIVER_API_KEY`
  - `AGENT_DRIVER_POSTGRES_DSN` (for opt-in PostgreSQL runtime store checks)

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
- Smolagents: [Building good Smolagents](https://smolagents.org/docs/building-good-smolagents/)
- Hugging Face: [smolagents source tree](https://github.com/huggingface/smolagents/tree/main/src/smolagents)
- Langfuse: [AI agent observability with Langfuse](https://langfuse.com/blog/2024-07-ai-agent-observability-with-langfuse)
- Langfuse: [Agent evaluation](https://langfuse.com/guides/cookbook/example_pydantic_ai_mcp_agent_evaluation)
- CoSAI: [Model Context Protocol security](https://github.com/cosai-oasis/ws4-secure-design-agentic-systems/blob/main/model-context-protocol-security.md)
