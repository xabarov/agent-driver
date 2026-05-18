# Agent Driver Implementation Roadmap

This roadmap updates the initial MVP order after reviewing current agent-runtime best practices. The main change: durable execution, interrupts, guardrails, and evaluation move earlier. Subagents and context compaction remain important, but they should sit on a reliable runtime foundation.

Additional smolagents review: keep the durable-first order, but make agent profiles, prompt templates, model-facing tool contracts, planning steps, and CodeAgent-style execution explicit instead of implicit in one generic ReAct loop. See [Smolagents lessons for agent profiles, prompts, and tools](architecture/smolagents-lessons.md).

Additional OpenClaude context review: treat context engineering as a layered runtime system, not a single "summarize the chat" feature. The strongest pattern is a five-layer stack: cheap deterministic tool-result microcompaction; background session-memory extraction; session-memory-based compaction that can skip an LLM summary; full LLM compaction with no-tool isolation, PTL retries, and structured prompt output; and partial/reactive compaction plus post-compact cleanup. Phases 6, 8, and 9 below should implement this stack incrementally.

Verification policy reference: all tool/runtime/context feature work must follow
[Testing and live trace policy](architecture/testing-and-live-trace-policy.md)
before merge (offline + live + trace-review gates).
Execution reference: [Test plan and coverage matrix](architecture/test-plan-and-matrix.md).

## Repository structure policy

Before adding non-trivial backend code, place it in an existing **package** that matches the phase below. Do not grow new flat `agent_driver/foo_bar.py` files next to an established package for the same concern (for example, extend `agent_driver/runtime/storage/` rather than adding `runtime/storage_extra.py`). Keep package `__init__.py` files as **facades**; implement in named submodules.

**Current project policy:** until this roadmap is completed end-to-end, do not introduce compatibility shims. Refactors must migrate imports/docs/tests to new paths in the same change and remove old module paths directly.

Reserved top-level packages (create when implementation starts; avoid empty directories):

| Phase | Focus | Primary package(s) |
| ----- | ----- | ------------------ |
| 2 / 2.5 | Durable runtime, checkpoint/event stores | `agent_driver.runtime`, `agent_driver.runtime.storage` |
| 3 | Tool registry, policy, governed executor | `agent_driver.tools`, `agent_driver.tools.executor` |
| 5 | Evaluation harness, deterministic runners | `agent_driver.evals` |
| 5 | Trace export, telemetry sinks | `agent_driver.observability` |
| 6 / 8 | Sessions, artifacts, planning, trimming, compaction (runtime) | `agent_driver.context` |
| 6 | Future context/session/artifact **contracts** | `agent_driver.contracts.context` |
| 7 | CodeAgent profile, sandbox | `agent_driver.code_agent` (create on phase start) |
| 9 | Subagent **orchestration** (not contract enums/models) | `agent_driver.subagents` (create on phase start) |
| 10 | MCP, HTTP/SSE, CLI | `agent_driver.adapters` (create on phase start) |

Cursor: see `.cursor/rules/repo-structure.mdc` for agent guidance on layout.

## Phase 0: Repository Bootstrap

Contract reference: [Phase 0 contracts spec](specs/phase-0-contracts.md).

- Create Python package skeleton.
- Add `pyproject.toml`, lint/format/test configuration.
- Define core contracts:
  - run input/output;
  - runtime events;
  - tool trace;
  - agent profile id;
  - action step and observation memory;
  - memory-step projection views;
  - prompt template id/version/hash;
  - executor serialization policy;
  - generated tool documentation;
  - subagent run row;
  - checkpoint id;
  - interrupt request;
  - resume command.
- Add fake LLM, fake tool, and local JSONL trace sink.

Exit criteria:

- contracts are importable and documented;
- event schemas have deterministic tests;
- prompt/profile/tool-doc contracts have snapshot tests;
- memory-step projection contracts cover full, succinct, and replay views;
- no external services required.

Implementation notes from tail catch-up pass:

- Phase 0 contract surface now includes dedicated modules for profile contracts
  (`profiles.py`), memory projections (`memory.py`), and executor-boundary
  serialization policy (`serialization.py`);
- runtime contracts now accept `agent_profile`, prompt-template metadata, and
  optional serialization policy in `AgentRunInput`;
- `AgentRunOutput` now includes optional `subagent_groups`,
  `memory_projection`, and `prompt_render` fields;
- subagent contracts now include `SubagentGroup` plus join/merge/group-status
  enums for future parallel orchestration phases.

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
- Add model-facing tool contract validation:
  - stable profile-compatible tool names;
  - argument descriptions and validation;
  - output type and optional JSON schema;
  - generated prompt/tool documentation;
  - failure remediation hints.
- Add prompt template registry:
  - template id/version;
  - required placeholders;
  - rendered prompt hash in traces;
  - profile compatibility metadata.
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

Implementation notes from first cut:

- `agent_driver/tools/` now provides initial governance primitives:
  - `ToolRegistry` backed by `ToolManifest`;
  - `evaluate_tool_policy(...)` with structured `allow|deny|interrupt` outcomes;
  - `GuardrailPipeline` with no-op defaults and block/sanitize decisions;
  - `GovernedToolExecutor` over deterministic `planned_tool_calls` metadata.
- Runtime keeps backward compatibility via `ToolExecutor` and
  `wrap_governed_executor(...)`.
- `SingleAgentRunner` now persists governed tool envelopes in run metadata and
  emits paused output with `interrupt_requested` when policy returns interrupt.
- Real side-effecting shell/filesystem/http tools remain deferred to a follow-up pass.
- Follow-up landed for filesystem writes:
  - `file_write` and `file_edit` were added as governed built-ins with
    `REVERSIBLE_WRITE` side-effect class, `MEDIUM` risk, and
    `ON_POLICY_MATCH` approval mode;
  - `file_write` supports `overwrite|append`, optional parent creation flag, and
    resulting-size byte budget guard;
  - `file_edit` enforces deterministic replacement with `expected_occurrences`
    mismatch protection to avoid accidental broad edits;
  - targeted unit and governed-executor tests now cover positive path, boundary
    contracts, and interrupt-on-risk policy behavior for write tools.
- First shell tool baseline landed:
  - `bash` was added as a governed built-in with policy-first read-only
    allowlist, destructive keyword blocking, and redirection blocking;
  - command execution runs with timeout kill-switch and bounded stdout/stderr
    previews;
  - runtime policy still treats `bash` as high-risk (`HIGH` + irreversible
    side-effect class) so medium/high approval thresholds interrupt before
    execution.

Implementation notes from tail catch-up pass:

- `ToolManifest` now carries model-facing fields (`args_schema`, `output_type`,
  `output_schema`, remediation hints, supported profiles) with profile-aware
  validation (including Python-identifier constraints for `code_agent`);
- deterministic prompt-facing tool docs were added in
  `agent_driver/tools/prompt_docs.py` with stable hash support;
- minimal prompt template registry was added in
  `agent_driver/tools/prompt_templates.py` with required-placeholder checks and
  `PromptRenderResult` hashing;
- governed tool envelopes now carry `agent_profile` and prompt-template
  metadata while keeping existing executor behavior backward-compatible.

OpenClaude tool import backlog:

- Treat `/home/roman/pyprojects/ML/openclaude/src/tools.ts` and
  `/home/roman/pyprojects/ML/openclaude/src/tools/` as the source inventory,
  but port contracts, policies, and algorithms instead of TypeScript/Ink UI
  implementations.
- Codebase and filesystem analysis tools, first wave:
  - `FileReadTool`: bounded text read with line windows, binary/media detection,
    image/PDF handling hooks, and stable truncation metadata;
  - `GlobTool`: fast path search with deterministic ordering and workspace
    boundary checks;
  - `GrepTool`: ripgrep-backed content search with ignored-path handling,
    context windows, result caps, and provenance;
  - `FileWriteTool` / `FileEditTool`: governed write and patch primitives behind
    approval-aware filesystem policy;
  - `NotebookEditTool`: targeted `.ipynb` cell edits as a separate filesystem
    tool, not an ad-hoc JSON edit path;
  - `LSPTool`: defer as a heavier code-intelligence project for definitions,
    references, hover, and symbols once process management exists.
- Programming and execution tools:
  - `BashTool`: import command-risk classification, read-only/destructive
    detection, path validation, output budgets, long-running task handoff, and
    git-safety rules before exposing a real shell;
  - `PowerShellTool`: defer until Windows support is explicit, but keep the
    same policy shape as shell;
  - `TaskOutputTool` / `MonitorTool`: model background process output as durable
    task artifacts with bounded previews;
  - `REPLTool`: defer unless CodeAgent needs a persistent interpreter beyond the
    current restricted executor.
- Web and research tools:
  - `WebFetchTool`: URL fetch with content-type checks, byte/token budgets,
    markdown extraction, robots/security policy, and optional provider backends;
  - `WebSearchTool`: provider interface for Tavily/Jina/Firecrawl/Mojeek/
    DuckDuckGo/custom search, with query limits and normalized result schema;
  - `WebBrowserTool`: do not build as a first native tool; prefer Playwright or
    browser MCP adapters first.
- MCP and external tool import:
  - `MCPTool`: generic manifest wrapper for MCP tool descriptors, preserving
    schemas, annotations, server provenance, and security policy;
  - `ListMcpResourcesTool` / `ReadMcpResourceTool`: resource discovery and read
    operations with the same approval and output-budget path as native tools;
  - `McpAuthTool`: explicit auth flow for servers that need OAuth or token setup;
  - `assembleToolPool` / `getMergedTools`: deterministic built-in + MCP merge
    with deny-rules and stable prompt-cache ordering.
- Planning, task, and human interaction tools:
  - `TodoWriteTool`: session-local structured checklist events;
  - `TaskCreateTool`, `TaskGetTool`, `TaskUpdateTool`, `TaskListTool`: durable
    task objects for larger multi-step work, separate from lightweight todos;
  - `AskUserQuestionTool`: structured choice questions for approval/clarify
    flows;
  - `EnterPlanModeTool` / `ExitPlanModeV2Tool`: map to Phase 6 planning state
    instead of making them independent side-effect tools.
- Subagent and collaboration tools:
  - `AgentTool`: map to Phase 9 `spawn_subagent` and child-run rows, not an
    opaque tool result;
  - `SendMessageTool`, `TeamCreateTool`, `TeamDeleteTool`, `ListPeersTool`: defer
    until the subagent orchestration model supports teammate sessions and swarms;
  - `EnterWorktreeTool` / `ExitWorktreeTool`: consider after filesystem/shell
    policy exists, because worktree changes are high-risk filesystem actions.
- Skills, workflows, and product automation:
  - `SkillTool`: support repository/user skill discovery as prompt/context input,
    with explicit trust and path provenance;
  - `ToolSearchTool`: defer until the manifest is large enough to require lazy
    tool discovery;
  - `BriefTool`: map to runtime messages plus artifact attachments, not a core
    execution primitive;
  - `WorkflowTool`, cron, remote trigger, PR subscription, push notification, and
    file-send tools are product automation adapters and should remain deferred
    until the core registry, context, and adapter layers are stable.

Exit criteria:

- high-risk tools can be blocked or interrupted;
- tool outputs are truncated/summarized with metadata;
- tool manifests render deterministic provider-native, ReAct, and CodeAgent-facing docs;
- profile-incompatible tool names/prompts fail validation;
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

Implementation notes from first cut:

- runner now persists `pending_interrupt` (interrupt + pending call/envelope) in
  checkpoint metadata and accepts resume by either `interrupt_id` (preferred) or
  legacy checkpoint id for backward-compatible resume flows;
- governed tool executor now emits richer `proposed_action` metadata for
  approval cards (`args_preview`, risk/side-effect/approval mode) and allows
  resume-cancel in `allowed_actions`;
- runtime resume handling now supports deterministic `approve|edit|reject|cancel|clarify`
  transitions with terminal reasons/events for reject/cancel and approved-call
  execution without duplicate interrupt loops;
- contracts now include `ApprovalPayload` helper for UI-facing approval cards,
  and runtime outputs expose this shape in metadata for paused and terminal
  envelopes.

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
- Add local devtools:
  - run replay from persisted events;
  - graph/profile/tool tree summary;
  - redaction-safe support bundle export.
- Add context-quality evaluation lane for Phase 6/8 context work:
  - deterministic needle-fact fixtures;
  - recall/hallucination/provenance/budget-efficiency scoring;
  - optional OpenRouter-backed live recall smoke.
  See [Test Plan and Coverage Matrix](architecture/test-plan-and-matrix.md#context-quality-matrix).

Exit criteria:

- local eval run works without external services;
- trace export works with no-op/local sink;
- evaluation can compare two runs on cost/latency/trajectory.
- one run can be rendered as full debug memory, succinct context view, and CLI replay.
- context-quality baseline can compare trim-only, microcompaction, digest/session
  memory, and future LLM-compaction strategies.

## Phase 6: Context Engineering

- Add session backend protocol.
- Implement in-memory and SQLite sessions.
- Add turn digest.
- Add artifact/context store protocol.
- Add context block/projection contracts for:
  - full debug memory;
  - model-facing working context;
  - compact summary messages;
  - artifact pointers;
  - replay views.
- Add planning/todo state tool.
- Add optional planning step prompt shaped around:
  - facts given;
  - facts learned;
  - facts still to look up;
  - facts still to derive;
  - next plan.
- Persist planning-step events separately from ordinary chat/tool observations.
- Add tool-result preview/artifact split.
- Add observation memory for model-facing stdout/stderr/tool-log previews.
- Add deterministic microcompaction for tool observations before model calls:
  - preserve tool call ids and provenance;
  - replace old large stdout/stderr/file/search/web results with bounded stubs;
  - record token estimates and bytes saved;
  - never split a tool call from its observation.
- Add deterministic context trimming.
- Add token-pressure accounting:
  - context-window estimate;
  - warning threshold;
  - compact threshold;
  - blocking threshold;
  - per-profile output-token reserve.

Exit criteria:

- long tool output goes to artifact store;
- prompt receives bounded preview plus pointer;
- plan state survives turns;
- planning updates are replayable and compactable separately from chat history;
- observations include provenance, trust labels, and truncation metadata;
- digest and artifact references are included in run metadata.
- microcompaction is deterministic, traceable, and safe for replay;
- trimming preserves tool/action invariants and never creates orphan observations;
- token-pressure state is emitted in run metadata and events.

Implementation notes from integration pass:

- runtime config now accepts injectable `session_store`, `artifact_store`, and
  `context_store` dependencies; defaults remain in-memory;
- output assembly persists session turns/digests and includes populated
  `digest_refs` and durable `artifact_refs` in metadata;
- dedicated planning events (`channel=planning`) are emitted from runtime path,
  enabling replay/compaction separation from ordinary chat/tool observations;
- LLM request assembly now applies deterministic context trimming and supports
  bounded observation previews before model completion.
- context-quality work is tracked in
  [Test Plan and Coverage Matrix](architecture/test-plan-and-matrix.md#context-quality-matrix);
  Phase 6 should be treated as deterministic context hygiene until those
  retention tests measure semantic summarization quality.

## Phase 7: CodeAgent Profile And Sandboxed Action Execution

- Add opt-in `code_agent` profile.
- Add sandboxed Python action executor abstraction.
- Add authorized import policy.
- Add safe executor-boundary serialization:
  - JSON-safe payloads by default;
  - explicit type markers for common rich values;
  - pickle/arbitrary object transfer disabled unless explicitly allowed.
- Add operation, loop, execution-time, and output-length limits.
- Add forbidden module/function and dunder-access checks.
- Capture stdout/stderr as bounded Observation memory.
- Expose tools as callable Python functions with generated signatures/docs.
- Require approval policies for filesystem, shell, network, and side-effecting calls.
- Add final-answer extraction contract for code blocks.

Exit criteria:

- fake CodeAgent can complete arithmetic and safe tool-composition eval cases;
- unsafe imports and side-effecting calls are blocked or interrupted;
- unsafe serialized payloads and forbidden interpreter operations fail closed;
- stdout/stderr observations are persisted and budgeted;
- action, observation, and final-answer events replay deterministically.

Implementation notes from first cut:

- new package `agent_driver/code_agent` added with explicit submodules for
  contracts, policy checks, serialization, executor, tool-surface rendering, and
  runtime profile adapter;
- `RunnerConfig` now supports `code_executor`, `code_limits`,
  `authorized_imports`, and `tool_registry` for opt-in `code_agent` runs;
- `FakeRestrictedCodeExecutor` enforces import/dunder/forbidden-call checks,
  execution/output limits, and bounded stdout/stderr observations;
- safe executor-boundary serialization uses `ExecutorSerializationPolicy` with
  fail-closed behavior unless unsafe mode is explicitly enabled;
- callable Python tool docs/signatures are generated deterministically from
  `ToolManifest` and reused by code-agent execution path;
- side-effecting tools in code-agent flow route through approval interrupts using
  existing policy interrupt payloads.
- planned follow-up backlog after first cut:
  [Phase 7 follow-ups from smolagents](architecture/phase7-smolagents-followups.md).

Implementation notes from follow-up pass (`7.2`-`7.4`):

- code-agent prompt assembly now uses dedicated deterministic renderer with
  explicit imports/tool-docs/final-answer contract and hashed prompt payload;
- runtime step transitions now support iterative `code_agent` loop
  (`llm_call -> tool_stage -> llm_call`) until `final_answer` is produced or
  terminal limits/interrupts are hit;
- replay and metadata paths now include richer projection payloads for
  inspectability (`prompt_render`/`tool_results_count` in memory projection);
- added subprocess-backed executor adapter behind existing
  `CodeActionExecutor` contract with hard wall-clock timeout and reliable
  process termination;
- subprocess adapter preserves compatibility by falling back to local executor
  when callable tools are present, while still enforcing fail-closed policy and
  bounded observations.

Implementation notes from follow-up pass (7.3/7.4):

- runtime `code_agent` path now supports iterative loop semantics
  (`llm_call -> tool_stage -> llm_call`) until `final_answer(...)` is produced,
  while preserving deterministic `max_steps` termination behavior;
- `CodeAgentStageResult` now carries explicit `has_final_answer`, allowing runtime
  step transitions to distinguish non-terminal code actions from terminal answers;
- new executor adapter `SubprocessRestrictedCodeExecutor` added behind existing
  `CodeActionExecutor` interface, with hard wall-clock timeout termination and
  compatible fallback to local restricted executor when callable tools are present;
- runtime memory projection now includes prompt/tool debug facets
  (`prompt_render` summary and `tool_results_count`) and replay CLI output
  surfaces these fields for operator-facing inspection without raw log digging.

## Phase 8: LLM Compaction

- Implement layered compaction orchestration inspired by OpenClaude:
  - first run deterministic microcompaction from Phase 6;
  - then try session-memory compaction when session memory is current enough;
  - then fall back to full LLM compaction;
  - later add partial/reactive compaction as a separate path.
- Add session-memory extraction:
  - background/forked run that updates durable session notes;
  - thresholds based on message tokens and tool-call count;
  - last-summarized turn/checkpoint tracking;
  - session-memory file/record stored as a first-class context artifact.
- Add session-memory-based compaction:
  - build a compacted run context from existing session memory;
  - preserve a bounded recent tail after the summary;
  - enforce minimum recent tokens/messages and maximum tail cap;
  - preserve action/observation pairs and streaming fragments that share ids;
  - skip full LLM compaction when this path is sufficient.
- Add LLM full-history compaction:
  - no-tool compact profile;
  - structured prompt with private drafting section and persisted summary section;
  - summary sections for request intent, key concepts, files/code, errors/fixes,
    solved/open problems, user messages, pending tasks, current work, and next step;
  - deterministic post-processing that strips draft/analysis text before reuse.
- Add context-quality eval comparison before enabling new compaction defaults:
  - trim-only baseline;
  - trim plus deterministic microcompaction;
  - trim plus session digest/session memory;
  - session-memory compaction;
  - full LLM compaction;
  - optional OpenRouter live lane for semantic recall.
- Add partial compaction:
  - summarize prefix while keeping recent suffix intact;
  - summarize recent suffix while keeping older context intact;
  - record the pivot/checkpoint and cache invalidation semantics.
- Add compaction eligibility audit.
- Add compaction lock.
- Add PTL-style retry by dropping oldest digest/API-round groups.
- Add sanitizers before compaction prompt:
  - strip or mark media blocks;
  - remove attachments that will be re-injected after compaction;
  - redact secrets using existing guardrails.
- Add post-compact cleanup:
  - clear microcompact state;
  - reset stale approval/cache/planning baselines where required;
  - re-inject active plan, selected artifacts, and relevant profile instructions
    under explicit token budgets.
- Add autocompact controls:
  - warning/error/compact thresholds;
  - output-token reserve per model/profile;
  - circuit breaker after repeated compaction failures;
  - trace events for skipped, successful, and failed compactions.

Exit criteria:

- compaction runs only when eligible;
- skipped compaction records reason;
- summary preserves required facts in eval cases;
- compaction trace includes model, latency, token/cost data.
- session-memory compaction can avoid a full LLM summary in deterministic tests;
- full compaction strips draft/analysis text and preserves the structured summary;
- PTL retry unblocks over-limit compaction without corrupting action/observation
  ordering;
- post-compact context contains active plan/artifact references under budget;
- autocompact stops retrying after repeated failures and records the circuit-breaker state.
- compaction changes improve or preserve context-quality baseline scores before
  they become default behavior.

## Phase 9: Subagents And Parallel Orchestration

- Add subagent contracts and run rows.
- Add `SubagentGroup` contract for parent fan-out/fan-in steps:
  - `group_id`;
  - parent run/checkpoint/step ids;
  - child run ids;
  - join policy;
  - shared deadline/budget;
  - terminal state;
  - merge provenance.
- Implement sync child graph execution.
- Add `spawn_subagent` tool.
- Add grouped spawn API for multiple child tasks.
- Add join policies:
  - `wait_all`;
  - `wait_any`;
  - `k_of_n`;
  - `best_effort_until_deadline`;
  - `race`;
  - `manual_review`.
- Add managed-agent facade:
  - `task` input;
  - typed `additional_args`/artifact references;
  - bounded child final-answer summary.
- Add context handoff and merge policy for child agents:
  - child receives a scoped model-facing context projection, not the full parent
    debug memory by default;
  - child outputs bounded final summary plus artifact references;
  - child session memory can be merged into parent only with provenance;
  - parent compact/replay views show which facts came from which child run.
- Add merge provenance.
- Add merge modes:
  - append;
  - rank;
  - synthesize;
  - vote;
  - manual.
- Add group budget and backpressure controls:
  - `max_parallel`;
  - per-child and group deadlines;
  - token/cost budget;
  - cancellation propagation.
- Add terminal-state handling.
- Add optional background local executor.

Exit criteria:

- parent run records child lifecycle;
- parent run records subagent group lifecycle and join policy;
- managed-agent calls create child run rows, not opaque tool traces;
- child output merges with provenance and partial-failure metadata;
- child context handoff and merged summaries are bounded, replayable, and
  provenance-preserving;
- retry after parent crash does not duplicate already-spawned children;
- `race` and cancellation policies stop pending/running children deterministically;
- failed/timed-out child does not leave stale running rows;
- subagent traces link to parent trace/run.

## Phase 10: MCP And API Adapters

- Add MCP client design/adapter.
- Import MCP tools into manifest.
- Map MCP `outputSchema` / structured content into `ToolManifest.output_schema`.
- Add MCP security policy controls.
- Add FastAPI/SSE adapter.
- Add CLI demo.
- Add example apps:
  - general assistant;
  - codebase assistant;
  - document-analysis assistant.

Exit criteria:

- MCP tools can be allowlisted and approval-gated;
- structured MCP tools preserve output schemas and descriptor audit metadata;
- SSE adapter uses typed runtime events;
- examples run against fake/local providers.

## Deferrals

Do not include in the first implementation:

- scientific paper tools;
- Neo4j/Qdrant assumptions;
- distributed worker backends;
- production Postgres checkpoint backend is deferred from first cut, but should be prioritized once multi-worker or shared API deployment is required;
- CodeAgent as the default loop or as an unsandboxed executor;
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
