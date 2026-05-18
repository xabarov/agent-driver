# Agent Driver: analysis and extraction plan

Date: 2026-05-18

## Document Map

This document is the high-level entry point. Details are split into focused notes:

- [Docs index](README.md)
- [Durable runtime, checkpointing, and worker execution](architecture/durable-runtime.md)
- [Human review, interrupts, and guardrails](architecture/hitl-and-guardrails.md)
- [Observability, evaluation, and regression harness](architecture/evaluation-and-observability.md)
- [Context engineering, tools, and MCP integration](architecture/context-tools-and-mcp.md)
- [Implementation roadmap](roadmap.md)

## Goal

`agent-driver` should become a clean Python LangGraph engine for building products with agentic chats. It should reuse the strongest ideas from `science-graphrag` and the Python adaptation of `openclaude`, but avoid inheriting scientific-domain coupling.

The engine should provide:

- traceable agent runs with Langfuse and Phoenix/OpenTelemetry support;
- a provider-agnostic LLM layer with health-aware routing and local model support;
- reusable tools for common application scenarios, not only literature research;
- subagent execution tools and subagent observability contracts;
- context-window compaction and durable conversation memory;
- a small app-facing API surface suitable for chat products.

The first milestone is not to copy the current `science_graphrag.agent` package as-is. It is to extract stable contracts and rebuild the engine around them with explicit extension points.

## Architecture Gap After External Review

After comparing this plan with current agent-runtime practices from LangGraph, OpenAI Agents SDK, Anthropic agent guidance, Langfuse evaluation guidance, Deep Agents, and MCP security discussions, the main gap is clear: the initial plan is strong as an extraction plan from `science-graphrag`, but not yet strong enough as a production agent engine blueprint.

The missing runtime foundations are:

- durable execution and checkpointing as a core contract, not a later storage detail;
- resume, replay, and branch-from-checkpoint semantics;
- human-in-the-loop interrupts and approval flows;
- guardrails as a separate input/tool/output policy pipeline;
- a run queue / worker execution model for request-independent long runs;
- evaluation datasets, trajectory checks, and regression baselines;
- planning/task state as a reusable primitive;
- context engineering beyond LLM compaction: artifact offloading, context isolation, and tool-result budgets;
- MCP integration and MCP-specific threat controls;
- an explicit decision model for deterministic workflows vs agentic loops.

This changes the MVP order. Checkpointing, interrupts, guardrails, and evaluation should arrive before complex subagents and LLM compaction. See [Implementation roadmap](roadmap.md).

External references:

- [Building LangGraph: Designing an Agent Runtime from First Principles](https://blog.langchain.com/building-langgraph)
- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview)
- [OpenAI Agents SDK](https://developers.openai.com/api/docs/guides/agents)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)
- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Langfuse: Agent evaluation](https://langfuse.com/guides/cookbook/example_pydantic_ai_mcp_agent_evaluation)
- [CoSAI: MCP security guidance](https://github.com/cosai-oasis/ws4-secure-design-agentic-systems/blob/main/model-context-protocol-security.md)

## Source Systems

### `openclaude` Python adaptation

The useful part of the Python `openclaude` adaptation is its minimal provider-routing layer:

- provider catalogue with health checks;
- latency/cost/error scoring;
- fallback after provider failures;
- mapping logical large/small model requests to provider-specific model IDs;
- OpenAI-compatible local backends such as Atomic Chat;
- Ollama message conversion and streaming.

This code is small and direct, but it is not yet an agent runtime. It should influence the `agent-driver` LLM gateway, not the graph architecture.

### `science-graphrag`

`science-graphrag` already contains a production-shaped LangGraph runtime:

- `science_graphrag/agent/runtime.py` acts as the sync facade and normalizes run outputs.
- `science_graphrag/agent/graph/state.py` defines the LangGraph state and prompt-memory injection.
- `science_graphrag/agent/graph/supervisor.py` implements supervisor/specialist routing.
- `science_graphrag/agent/tool_execution_pipeline.py` centralizes tool policy, tool execution, deadlines, sidechain transcripts, and tool result normalization.
- `science_graphrag/agent/graph/tracing.py` extracts tool traces from LangGraph messages.
- `science_graphrag/agent/subagents/runtime.py` defines explicit child-run rows, terminal states, merge provenance, and hook emission.
- `science_graphrag/agent/context/llm_history_compact.py` and adjacent context modules implement multi-turn digest compaction.
- `science_graphrag/observability/` contains Phoenix/OpenTelemetry span helpers.

The valuable asset is not individual domain tools like paper search, graph queries, or bibliography formatting. The valuable asset is the set of runtime seams that survived real agent work: normalized run output, state metadata, tool policy, subagent rows, compaction audit, salvage paths, and trace-review artifacts.

## Proposed Product Boundary

`agent-driver` should be a library/runtime package, not an application. It should not know about scientific papers, workspaces, Neo4j schemas, bibliography styles, or the `science-graphrag` UI. Applications should bring their own tools, storage adapters, prompts, and HTTP/UI layer.

The engine owns:

- conversation/run lifecycle;
- graph construction primitives;
- model provider abstraction;
- tool registry and tool execution policy;
- subagent spawn/merge contracts;
- memory and compaction policy;
- tracing and run metadata;
- streaming event envelope;
- test harnesses for runtime contracts.

Applications own:

- domain tools;
- product-specific prompts;
- authentication and user/workspace models;
- storage implementation choices;
- UI rendering;
- deployment-specific API shape.

## Candidate Package Shape

```text
agent_driver/
  config.py
  runtime/
    facade.py
    contracts.py
    events.py
    salvage.py
    replay.py
    branching.py
  checkpointing/
    protocol.py
    memory.py
    sqlite.py
    postgres.py
  execution/
    runner.py
    queue.py
    worker.py
    leases.py
    cancellation.py
  graph/
    state.py
    builder.py
    supervisor.py
    react_edges.py
  llm/
    providers.py
    router.py
    chat.py
    streaming.py
  tools/
    base.py
    registry.py
    manifest.py
    execution.py
    guardrails.py
    common/
      web.py
      filesystem.py
      shell.py
      memory.py
      browser.py
  subagents/
    contracts.py
    runtime.py
    tools.py
    merge.py
    hooks.py
  memory/
    session.py
    backends.py
    digest.py
    compaction.py
    sanitizers.py
  context/
    artifacts.py
    planning.py
    budgets.py
  mcp/
    client.py
    discovery.py
    tool_adapter.py
    security.py
  observability/
    spans.py
    phoenix.py
    langfuse.py
    attributes.py
  evals/
    datasets.py
    runners.py
    evaluators.py
    baselines.py
  api/
    sse.py
    payloads.py
  testing/
    fake_llm.py
    fake_tools.py
    trace_assertions.py
```

This split keeps graph mechanics, LLM routing, tool execution, subagent handling, memory, and observability independently testable.

## Core Runtime Contracts

### Agent Run Input

The app should call the engine with a compact structured request:

- `thread_id`;
- `user_id` / `workspace_id` as opaque metadata;
- `messages` or latest `input`;
- `agent_id` / graph preset;
- `tool_policy`;
- `max_tool_calls`;
- `deadline`;
- `run_id` / idempotency key when supplied by the app;
- resume/checkpoint pointer when continuing a paused run;
- optional app metadata.

The engine should not require a particular product workspace model.

### Agent Run Output

Use a normalized output similar to `AgentRunOutput`, but domain-neutral:

- `answer`;
- `messages`;
- `events`;
- `tool_trace`;
- `subagent_runs`;
- `usage`;
- `warnings`;
- `trace_ids`;
- `checkpoint_id`;
- `memory_audit`;
- `debug_events`;
- `artifacts`;
- `interrupt_request`;
- `terminal_reason`.

The output should be identical in spirit for sync and streaming runs. The app API can decide how to expose it.

### Event Envelope

Streaming should use typed internal events first, then adapters:

- `run_started`;
- `token_delta`;
- `tool_call_started`;
- `tool_call_completed`;
- `subagent_started`;
- `subagent_completed`;
- `memory_compacted`;
- `checkpoint_saved`;
- `interrupt_requested`;
- `run_paused`;
- `run_resumed`;
- `warning`;
- `run_completed`;
- `run_failed`.

SSE should be an adapter, not the runtime primitive. This keeps the engine usable from FastAPI, CLI, workers, tests, or future WebSocket APIs.

### Durable Execution

The engine should treat checkpoints as first-class runtime objects. Each long-running graph must be resumable from a saved state after crash, cancellation, approval pause, or worker restart.

Required contracts:

- checkpoint backend protocol;
- `checkpoint_id` in events and outputs;
- resume from latest or specific checkpoint;
- branch/fork from checkpoint for debugging and evals;
- idempotency rules for side-effecting tools;
- replay support for regression tests.

See [Durable runtime, checkpointing, and worker execution](architecture/durable-runtime.md).

### Human Review And Guardrails

High-risk tools and policy violations should not be handled by ad hoc exceptions. The runtime should support persisted interrupts:

- approve/reject/edit tool calls;
- ask for clarification;
- patch state and resume;
- cancel the run;
- trace every decision.

Guardrails should be separate stages around input, prompt/context, tool args, tool results, and final output. See [Human review, interrupts, and guardrails](architecture/hitl-and-guardrails.md).

## LLM Layer

The engine should merge two ideas:

- `openclaude` style provider routing: health, latency, cost, error-rate scoring, fallback, local providers.
- `science-graphrag` style shared chat construction and side-LLM execution: usage telemetry, prompt-cache telemetry, concurrency gates, and safe message normalization.

Initial provider targets:

- OpenAI-compatible HTTP endpoint;
- OpenRouter;
- Anthropic;
- Ollama;
- local OpenAI-compatible server.

The public model selector should use logical roles:

- `default`;
- `large`;
- `small`;
- `side`;
- `embedding` later, if the engine grows retrieval helpers.

Provider-specific model IDs should live behind configuration.

## Observability

Tracing should be first-class from the first implementation. The engine needs a neutral span vocabulary and exporters for Phoenix/OpenTelemetry and Langfuse.

Minimum span hierarchy:

- `agent.run`;
- `agent.graph.node`;
- `agent.llm.call`;
- `agent.tool.call`;
- `agent.subagent.run`;
- `agent.memory.compaction`.

Minimum attributes:

- `agent.driver.version`;
- `agent.id`;
- `thread.id`;
- `run.id`;
- `user.id` as optional opaque metadata;
- `model.provider`;
- `model.name`;
- `tool.name`;
- `tool.policy`;
- `subagent.id`;
- `terminal.reason`;
- input/output summaries with redaction controls.

Phoenix/OpenTelemetry can follow the existing `science-graphrag` span helper approach. Langfuse should be a separate adapter so applications can enable one or both without changing runtime logic.

## Tool System

The tool layer should preserve the `science-graphrag` lesson: tool execution is not just `ToolNode`.

The reusable tool execution seam should own:

- tool name normalization;
- manifest metadata;
- risk level;
- allowlist/denylist by mode and turn;
- per-tool deadlines;
- structured error payloads;
- debug events;
- trace extraction;
- output compaction/summarization;
- optional sidechain transcripts for long or nested work.
- approval policy and interrupt payloads for high-risk calls;
- idempotency metadata for side-effecting calls.

### Common Tool Families

Initial reusable tools should be conservative:

- web search / web fetch with allowlist and timeout controls;
- filesystem read/search/write tools with sandbox boundaries;
- shell command tool with timeout and explicit risk policy;
- HTTP request tool with SSRF protections;
- memory/session tools;
- artifact creation/update tools;
- subagent spawn and collect tools.
- planning/todo state tools;
- artifact/context store tools.

Scientific tools from `science-graphrag` should stay outside the core and later move into an optional package, for example `agent_driver_science`.

MCP tools should be supported as optional imported tools, not blindly trusted local capabilities. See [Context engineering, tools, and MCP integration](architecture/context-tools-and-mcp.md).

## Subagents

Subagents should be represented as explicit child runs, not as incidental tool calls.

Required contract:

- stable `task_id`;
- `subagent_id`;
- `task_type`;
- `description`;
- `execution_mode`: `sync` or `background`;
- `terminal_state`: `succeeded`, `failed`, `cancelled`, `killed`, `timed_out`;
- `latency_ms`;
- `tokens`;
- `cost_usd_estimate`;
- `failure_code`;
- `output_pointer`;
- `merge_provenance`.

This mirrors the strongest parts of `science_graphrag.agent.subagents.runtime`, but should be stripped of research-specific result shapes.

Initial execution modes:

- in-process sync child graph;
- background task interface with a local in-memory executor;
- later: process, queue, or remote executor adapters.

## Context And Compaction

The engine should support long-running chats from the start:

- per-turn digest after each completed run;
- session summary;
- optional thread insight;
- deterministic trim before LLM compaction;
- LLM full-history compaction with eligibility audit;
- compaction lock;
- PTL-style retry by dropping oldest digest groups on context-limit errors;
- sanitizers before passing memory into compaction prompts.

The `science-graphrag` L4 compaction pattern is worth extracting, but the prompt must become domain-neutral. It should preserve user goals, decisions, constraints, open questions, artifacts, tool outcomes, and unresolved tasks.

Compaction is not the whole context strategy. The engine also needs artifact offloading, planning state, tool-result budgets, and context isolation through subagents. See [Context engineering, tools, and MCP integration](architecture/context-tools-and-mcp.md).

## Graph Presets

The engine should offer several graph presets rather than one hard-coded supervisor:

- `single_react`: one ReAct agent with tools;
- `supervisor`: supervisor routes to named specialists;
- `planner_executor`: planner emits tasks, executor performs them, writer summarizes;
- `chat_only`: no tools, memory-aware assistant;
- `custom`: application supplies a graph builder.

The graph state should be generic and typed. Apps should be able to extend it with their own fields while keeping core runtime metadata stable.

The docs and examples should explicitly distinguish deterministic workflows from agentic loops:

- use workflow when the task path is known;
- use agent loop when the model must choose tools or adapt strategy;
- use handoff/subagent when a branch needs distinct instructions, tools, or isolated context;
- use agents-as-tools when the parent should retain synthesis control.

## Evaluation And Regression

Testing utilities are not enough. The engine needs an evaluation harness that can score:

- final answer quality;
- trajectory quality;
- individual step correctness;
- tool policy compliance;
- checkpoint/replay consistency;
- cost and latency budgets.

The first evaluators should be deterministic and trace-based. LLM-as-judge can come later. See [Observability, evaluation, and regression harness](architecture/evaluation-and-observability.md).

## What Not To Extract

Do not move these into the first engine:

- paper/work/citation schemas;
- Neo4j/Qdrant scholarly retrieval assumptions;
- bibliography formatting;
- arXiv/OpenAlex/Unpaywall tools as core tools;
- `science-graphrag` UI payload details;
- live-check scripts tied to the current product;
- compatibility shims for old `science-graphrag` runtime names.

These can become examples or optional extension packages later.

## MVP Sequence

The updated implementation sequence is maintained in [Implementation roadmap](roadmap.md). The high-level shape is:

- bootstrap contracts;
- LLM gateway;
- durable single-agent runtime with checkpoints;
- tool governance and guardrails;
- human-in-the-loop interrupts;
- observability and evaluation harness;
- context engineering and artifacts;
- LLM compaction;
- subagents;
- MCP and API adapters.

## Early Design Decisions

1. Keep `agent-driver` domain-neutral.
2. Make tracing and run metadata part of the core contract, not a later bolt-on.
3. Treat tools as governed capabilities with policy, risk, timeouts, and structured traces.
4. Treat subagents as child runs with lifecycle and merge provenance.
5. Treat compaction as a runtime subsystem with auditability, not a prompt hack.
6. Keep sync and streaming paths contract-compatible.
7. Prefer adapters over hard dependencies for Langfuse, Phoenix, storage, and web APIs.
8. Treat checkpointing/resume as a runtime invariant for non-trivial runs.
9. Treat human review and guardrails as first-class runtime flows.
10. Treat evaluation as part of the engine, not an external afterthought.

## Open Questions

- Should the package target LangChain/LangGraph interfaces directly, or wrap them behind `agent-driver` protocols?
- Should persistent memory start with SQLite, Postgres, Redis, or only protocol plus in-memory implementation?
- Should background subagents be included in the MVP or delayed until sync subagents are stable?
- Should filesystem/shell tools be in core or in an optional `agent-driver-tools-local` package?
- How much of the current `science-graphrag` SSE envelope should become a reusable API contract?
- Should model routing be global per process, per tenant, or per app instance?
- Should Langfuse tracing be implemented through OpenTelemetry where possible, or through native Langfuse SDK calls?
- Which checkpoint backends should be in the first public version: SQLite only, or SQLite plus Postgres protocol?
- Should MCP support be implemented in v1 or only designed in v1?
- Should planning/todo state be always available, or only enabled by graph preset?
- What is the default approval policy for local shell/filesystem tools?

### Updates After First Phase-2 Implementation

After implementing the first durable runtime cut, several decisions are now clearer:

- storage protocols are already stable enough to add additional DB adapters without changing runner contracts;
- SQLite + in-memory baseline is proven by replay/resume tests and should remain the default local path;
- next backend priority should be PostgreSQL (for multi-worker/shared API deployments), not Redis as primary checkpoint storage;
- Redis is better treated as optional queue/lease acceleration, while durable checkpoint history remains SQL-backed;
- documentation should keep backend conformance criteria explicit to prevent drift between adapters.

New practical architecture note:

- backend-specific logic should stay inside runtime store adapters only; `SingleAgentRunner` and event/checkpoint contracts must remain backend-agnostic.

## Recommended First Cut

The first implementation should be intentionally small:

- package skeleton;
- contracts;
- OpenAI-compatible/Ollama LLM gateway;
- `single_react` graph;
- checkpoint protocol with memory/SQLite backends;
- governed tool registry;
- basic guardrail pipeline;
- interrupt/resume contracts;
- OpenTelemetry span helper;
- in-memory session backend;
- deterministic turn digest;
- local trace/eval runner;
- fake LLM tests.

After that works, extract LLM compaction and subagent rows. This reduces the risk of moving the most complex `science-graphrag` machinery before the neutral durable runtime boundary is proven.
