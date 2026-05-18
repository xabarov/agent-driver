# Agent Driver Implementation Roadmap

This roadmap updates the initial MVP order after reviewing current agent-runtime best practices. The main change: durable execution, interrupts, guardrails, and evaluation move earlier. Subagents and LLM compaction remain important, but they should sit on a reliable runtime foundation.

## Phase 0: Repository Bootstrap

Contract reference: [Phase 0 contracts spec](specs/phase-0-contracts.md).

- Create Python package skeleton.
- Add `pyproject.toml`, lint/format/test configuration.
- Define core contracts:
  - run input/output;
  - runtime events;
  - tool trace;
  - subagent run row;
  - checkpoint id;
  - interrupt request;
  - resume command.
- Add fake LLM, fake tool, and local JSONL trace sink.

Exit criteria:

- contracts are importable and documented;
- event schemas have deterministic tests;
- no external services required.

## Phase 1: LLM Gateway

- Implement OpenAI-compatible provider.
- Implement Ollama provider.
- Add health-aware router based on the `openclaude` idea.
- Normalize sync and streaming chat responses.
- Capture usage, provider metadata, and prompt-cache fields when available.
- Harden streaming behavior and router fallback semantics for stream startup failures.
- Add optional live/network adapter checks (skipped by default in local suites).

Exit criteria:

- fake provider tests;
- provider health tests;
- streaming normalization tests;
- router fallback tests.
- router supports both `complete` and `stream`.
- streaming adapters have offline mocked tests validating progressive chunk emission.
- default test suite stays offline; live checks require explicit opt-in marker/env.

## Phase 2: Durable Single-Agent Runtime

- Build durable `SingleAgentRunner` with explicit step loop.
- Add storage protocols for checkpoints/events.
- Add in-memory checkpoint backend and event log.
- Add SQLite checkpoint+event backend for local replay tests.
- Save checkpoint after each successful runtime step.
- Resume from latest/explicit checkpoint with `run_resumed` event.
- Add cancellation probe and run limits (`deadline_seconds`, `max_steps`).
- Emit typed events with stable `run_id`, monotonic `seq`, and checkpoint references.
- Add minimal `ToolExecutor` protocol and noop implementation for Phase 3 prep.
- Document backend-neutral checkpoint/event storage contract boundaries for future DB adapters.

Exit criteria:

- run resumes after simulated post-checkpoint failure;
- run can be cancelled and timed out;
- max-step budget transitions to terminal failed state deterministically;
- checkpoint replay works with in-memory and sqlite stores;
- tool stage seam exists via `ToolExecutor` without full governance coupling.
- docs record explicit criteria for adding new checkpoint backends.

## Phase 3: Tool Governance And Guardrails

- Add tool registry and manifest.
- Add tool execution seam with:
  - name normalization;
  - allowlist/denylist;
  - risk level;
  - per-tool timeout;
  - structured errors;
  - output budgets.
- Add guardrail pipeline:
  - input;
  - prompt/context;
  - tool args;
  - tool result;
  - final output.
- Add approval-aware policies for shell/filesystem/HTTP tools.

Exit criteria:

- high-risk tools can be blocked or interrupted;
- tool outputs are truncated/summarized with metadata;
- guardrail decisions are traceable;
- retry of side-effecting tools requires idempotency or explicit policy.

## Phase 4: Human-In-The-Loop

- Implement `InterruptRequest`.
- Persist pending interrupt in checkpoint state.
- Implement `ResumeCommand`.
- Support approve/reject/edit/cancel/clarify flows.
- Add UI-facing approval payload shape.

Exit criteria:

- run pauses before high-risk tool call;
- run resumes after approval;
- edited tool args are applied and traced;
- rejection produces a terminal or alternate path.

## Phase 5: Observability And Evaluation Harness

- Add OpenTelemetry/Phoenix exporter.
- Add Langfuse exporter.
- Add deterministic evaluators:
  - event schema;
  - terminal state;
  - tool policy;
  - checkpoint/replay;
  - cost/latency budget.
- Add dataset runner.
- Add baseline report format.

Exit criteria:

- local eval run works without external services;
- trace export works with no-op/local sink;
- evaluation can compare two runs on cost/latency/trajectory.

## Phase 6: Context Engineering

- Add session backend protocol.
- Implement in-memory and SQLite sessions.
- Add turn digest.
- Add artifact/context store protocol.
- Add planning/todo state tool.
- Add tool-result preview/artifact split.
- Add deterministic context trimming.

Exit criteria:

- long tool output goes to artifact store;
- prompt receives bounded preview plus pointer;
- plan state survives turns;
- digest and artifact references are included in run metadata.

## Phase 7: LLM Compaction

- Add LLM full-history compaction.
- Add compaction eligibility audit.
- Add compaction lock.
- Add PTL-style retry by dropping oldest digest groups.
- Add sanitizers before compaction prompt.

Exit criteria:

- compaction runs only when eligible;
- skipped compaction records reason;
- summary preserves required facts in eval cases;
- compaction trace includes model, latency, token/cost data.

## Phase 8: Subagents

- Add subagent contracts and run rows.
- Implement sync child graph execution.
- Add `spawn_subagent` tool.
- Add merge provenance.
- Add terminal-state handling.
- Add optional background local executor.

Exit criteria:

- parent run records child lifecycle;
- child output merges with provenance;
- failed/timed-out child does not leave stale running rows;
- subagent traces link to parent trace/run.

## Phase 9: MCP And API Adapters

- Add MCP client design/adapter.
- Import MCP tools into manifest.
- Add MCP security policy controls.
- Add FastAPI/SSE adapter.
- Add CLI demo.
- Add example apps:
  - general assistant;
  - codebase assistant;
  - document-analysis assistant.

Exit criteria:

- MCP tools can be allowlisted and approval-gated;
- SSE adapter uses typed runtime events;
- examples run against fake/local providers.

## Deferrals

Do not include in the first implementation:

- scientific paper tools;
- Neo4j/Qdrant assumptions;
- distributed worker backends;
- production Postgres checkpoint backend is deferred from first cut, but should be prioritized once multi-worker or shared API deployment is required;
- LangSmith exporter, unless it becomes a target integration;
- complex LLM-as-judge evaluation before deterministic evals exist.

## Phase 2.5: Persistent Backend Expansion (Checkpoint/Event Stores)

Goal: add first production-grade persistent backend without changing runtime contracts.

- Implement `PostgresRuntimeStore` behind existing `CheckpointStore` / `RuntimeEventLog`.
- Add schema migration strategy (bootstrap DDL + versioned SQL migrations in app pipeline).
- Add backend conformance tests shared with SQLite/in-memory.
- Add retention and indexing guidance for long-lived runs.
- Add operational notes for transaction isolation and connection pooling.

Exit criteria:

- PostgreSQL backend passes the same deterministic replay/resume suite as SQLite.
- no runtime API changes required for switching SQLite -> Postgres.
- docs include backend selection matrix (local, single-node, multi-worker, managed cloud).

Implementation notes from first cut:

- storage protocols now include `list_checkpoints(...)`, `snapshot_debug()`, and `capabilities()`;
- runtime now includes store factory + env config + preflight helper for app integration;
- Postgres support is optional extra dependency (`.[postgres]`), base install remains lightweight;
- live PostgreSQL checks remain opt-in and skipped by default without env/DSN.
