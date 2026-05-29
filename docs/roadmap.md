# Agent Driver Implementation Roadmap

This roadmap updates the initial MVP order after reviewing current agent-runtime best practices. The main change: durable execution, interrupts, guardrails, and evaluation move earlier. Subagents and context compaction remain important, but they should sit on a reliable runtime foundation.

Additional smolagents review: keep the durable-first order, but make agent profiles, prompt templates, model-facing tool contracts, planning steps, and CodeAgent-style execution explicit instead of implicit in one generic ReAct loop. See [Smolagents lessons for agent profiles, prompts, and tools](architecture/smolagents-lessons.md).

Additional OpenClaude context review: treat context engineering as a layered runtime system, not a single "summarize the chat" feature. The strongest pattern is a five-layer stack: cheap deterministic tool-result microcompaction; background session-memory extraction; session-memory-based compaction that can skip an LLM summary; full LLM compaction with no-tool isolation, PTL retries, and structured prompt output; and partial/reactive compaction plus post-compact cleanup. Phases 6, 8, and 9 below should implement this stack incrementally.

Verification policy reference: all tool/runtime/context feature work must follow
[Testing and live trace policy](architecture/testing-and-live-trace-policy.md)
before merge (offline + live + trace-review gates).
Execution reference: [Test plan and coverage matrix](architecture/test-plan-and-matrix.md).

Additional SDK/SSE/CLI analysis update: the runtime already has durable typed
events and provider-level streaming primitives, but external usage is still
low-level (`SingleAgentRunner` + stores + tool wiring). The next architecture
increment should add a first-class app-facing SDK facade and a transport-neutral
runtime stream projection that both FastAPI/SSE and CLI can reuse. SSE remains
an adapter over durable runtime events rather than the runtime core protocol.

Additional OpenClaude improvement workstream: force planning, dialogue
steerability, and subagent orchestration should be developed as a focused
cross-phase initiative rather than copied wholesale from OpenClaude. See
[OpenClaude improvement plan](openclaude-improvement-plan-2026-05-29.md) for
the detailed source analysis, gap analysis, implementation phases, and exit
criteria.

## Repository structure policy

Before adding non-trivial backend code, place it in an existing **package** that matches the phase below. Do not grow new flat `agent_driver/foo_bar.py` files next to an established package for the same concern (for example, extend `agent_driver/runtime/storage/` rather than adding `runtime/storage_extra.py`). Keep package `__init__.py` files as **facades**; implement in named submodules.

**Current project policy:** until this roadmap is completed end-to-end, do not introduce compatibility shims. Refactors must migrate imports/docs/tests to new paths in the same change and remove old module paths directly.

Reserved top-level packages (create when implementation starts; avoid empty directories):

| Phase | Focus | Primary package(s) |
| ----- | ----- | ------------------ |
| 2 / 2.5 | Durable runtime, checkpoint/event stores | `agent_driver.runtime`, `agent_driver.runtime.storage` |
| 3 | Tool registry, policy, governed executor | `agent_driver.tools`, `agent_driver.tools.executor` |
| 3 | App-facing SDK surface and tool-set ergonomics | `agent_driver.sdk` (create on phase start) |
| 5 | Evaluation harness, deterministic runners | `agent_driver.evals` |
| 5 | Trace export, telemetry sinks | `agent_driver.observability` |
| 6 / 8 | Sessions, artifacts, planning, trimming, compaction (runtime) | `agent_driver.context` |
| 6 | Future context/session/artifact **contracts** | `agent_driver.contracts.context` |
| 7 | CodeAgent profile, sandbox | `agent_driver.code_agent` (create on phase start) |
| 9 | Subagent **orchestration** (not contract enums/models) | `agent_driver.subagents` (create on phase start) |
| 10 | Runtime stream adapters, MCP, HTTP/SSE, CLI | `agent_driver.adapters`, `agent_driver.contracts.stream`, `agent_driver.runtime.stream` (create on phase start) |

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
- Add policy-aware tool-choice scoring and antipattern detection in
  `agent_driver.tools.policy.scoring`:
  - `ToolChoicePolicyRegistry` composes plain-callable preference rules
    (return `(score_delta, reason)`) and antipattern rules (return
    `AntipatternMatch | None`) with isolated failure handling
    (`rule_error:<id>:<Exc>` and `rule_invalid_delta:<id>` synthetic
    reasons; synthetic `rule_error:<id>` / `rule_invalid_return:<id>`
    antipattern matches);
  - reference built-ins ship one rule per direction
    (`prefer_specialized_over_generic` via `manifest.metadata["capabilities"]`
    and `generic_after_specialized_search` with configurable name sets);
  - `antipattern_to_warning_payload(match)` projects into the same
    `RuntimeEventType.WARNING` contract used by `kind="token_pressure"`,
    and `agent_driver.adapters.project_warning_event` recognizes the
    new `kind="tool_choice_antipattern"` so SSE consumers get one
    stable warning vocabulary;
  - documented in `docs/architecture/tool-choice-policy.md`.
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

Implementation status policy for imported tools:

- `native`: runtime executes real behavior inside `agent-driver`;
- `session_local_state`: behavior is intentionally local/in-memory only;
- `request_envelope`: tool emits intent payload for future adapter execution;
- `platform_gated_native`: native path exists but depends on host binaries/runtime.

- Treat `/mnt/share/gitlab_projects/openclaude/src/tools.ts` and
  `/mnt/share/gitlab_projects/openclaude/src/tools/` as the source inventory,
  but port contracts, policies, and algorithms instead of TypeScript/Ink UI
  implementations.
- Imported in this stage:
  - `McpAuthTool` as built-in `mcp_auth` with token/oauth payload handling;
  - `EnterPlanModeTool` / `ExitPlanModeV2Tool` as `enter_plan_mode` and
    `exit_plan_mode_v2`, mapped to planning state updates;
  - `SkillTool` as read-only `skill_tool` with SKILL.md discovery, trust
    classification, and path provenance metadata;
  - `ToolSearchTool` as built-in `tool_search` for local manifest discovery by
    name/description plus risk/side-effect filters;
  - `BriefTool` as built-in `brief_tool` for runtime brief payloads with
    attachment references (`artifact_ref`) and channel metadata;
  - `AgentTool` as built-in `agent_tool` that emits a structured
    `spawn_subagent` request envelope (task, mode, idempotency, metadata);
  - `SendMessageTool` as session-local `send_message_tool` queue payload,
    keeping teammate messaging local until full orchestration sessions land;
  - `ListPeersTool` as session-local `list_peers_tool` for peer directory
    discovery (status/capability filters) before full teammate sessions;
  - `TeamCreateTool` / `TeamDeleteTool` as session-local team registry
    primitives (`team_create_tool`, `team_delete_tool`) with reversible state;
  - `TeamGetTool` / `TeamListTool` as read-only team registry accessors
    (`team_get_tool`, `team_list_tool`) for deterministic lookup/filter flows;
  - `TaskStopTool` as `task_stop_tool`, `MonitorTool` as `monitor_tool`, and
    bounded `sleep_tool` runtime helper;
  - `EnterWorktreeTool` / `ExitWorktreeTool` as request-envelope tools
    (`enter_worktree_tool`, `exit_worktree_tool`);
  - `PowerShellTool` baseline as `powershell_tool` with explicit unavailable
    behavior when `pwsh` is absent;
  - lightweight `LSPTool` baseline as read-only `lsp_tool`;
  - `WorkflowTool`, cron adapters, remote trigger, PR subscription, push
    notification, and file-send adapters as local intent tools.
  - Explicitly deferred (internal/test/interactive): `TestingPermissionTool`,
    `OverflowTestTool`, `CtxInspectTool`, `TerminalCaptureTool`, `SnipTool`,
    `VerifyPlanExecutionTool`, `SuggestBackgroundPRTool`.
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
  - `PowerShellTool`: baseline implemented as policy-compatible sibling shell
    tool; Windows-native semantics remain deferred;
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
  - `assembleToolPool` / `getMergedTools`: baseline deterministic built-in + MCP
    merge helper is implemented; deeper runtime integration can evolve further.
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
  - `SendMessageTool`, `TeamCreateTool`, `TeamDeleteTool`, `ListPeersTool`,
    `TeamGetTool`, `TeamListTool` are now implemented as session-local
    transitional tools; full teammate sessions/swarms remain deferred;
  - `EnterWorktreeTool` / `ExitWorktreeTool`: consider after filesystem/shell
    policy exists, because worktree changes are high-risk filesystem actions
    (current baseline uses request-envelope tools, not direct mutations).
- Skills, workflows, and product automation:
  - `SkillTool`: support repository/user skill discovery as prompt/context input,
    with explicit trust and path provenance;
  - `ToolSearchTool`: basic local manifest search is implemented; defer
    cross-registry lazy/distributed discovery until manifest/provider volume
    justifies it;
  - `BriefTool`: implemented as a lightweight runtime message + attachment
    envelope; defer deeper workflow/notification integrations to product
    automation adapters;
  - `WorkflowTool`, cron, remote trigger, PR subscription, push notification, and
    file-send baseline tools are implemented as local intent adapters; external
    service execution remains deferred until adapter layers are production-ready.

SDK ergonomics and user extension backlog (Phase 3 follow-up):

- Add a first-class `ToolSet` / tool-pack concept on top of `ToolRegistry`:
  - create a registry from explicit tools only;
  - compose named built-in packs such as filesystem-read, filesystem-write, web,
    shell, planning, tasking, and MCP;
  - filter a pack by names, risk, side-effect class, supported profile, or
    application tag before tools are rendered to the model.
- Add app-facing SDK facade contracts in `agent_driver.sdk`:
  - `create_agent(...)` helper that wires provider, runtime stores, tool registry,
    and governed executor defaults without manual low-level plumbing;
  - `Agent` facade methods (`run`, `stream`, `resume`) that preserve direct access
    to low-level runtime APIs for advanced embedders;
  - explicit environment/config bootstrap for local development and backend apps.
- Separate two user-facing concepts clearly in API and docs:
  - tool-set selection controls the model-visible and executable tool surface;
  - per-run `ToolPolicyInput.allowed_tools` / `denied_tools` remains a safety
    guard that can deny execution even when a tool exists in the registry.
- Add a high-level runner/agent construction helper so application code does not
  have to manually keep `tool_registry` and `GovernedToolExecutor` in sync, for
  example `create_agent(..., tools=ToolSet.only("web_fetch"))` or equivalent.
- Add an ergonomic custom-tool registration path:
  - decorator or builder for typed Python functions;
  - automatic JSON-schema extraction where practical;
  - explicit overrides for name, description, risk, side-effect class, approval
    mode, timeouts, output budgets, and supported profiles;
  - validation that generated model-facing docs include argument descriptions and
    failure remediation hints.
- Add a declarative external-contract registration path (for hosts whose tool
  catalogue is already a structured spec, not Python functions):
  - `manifest_from_contract(contract: Mapping) -> ToolManifest` accepts a
    flexible mapping (name, description, risk/intrusiveness aliases,
    side-effect, approval hints, timeouts, schemas, supported profiles,
    metadata) and normalizes it into a validated manifest;
  - `register_contract_tool(registry, contract, async_handler)` wires the
    manifest with a caller-supplied async handler;
  - host-specific extras (`queue_category`, `intrusiveness`, `cost`,
    `requires_trigger`, `capabilities`, `stage_tags`, etc.) pass through
    `metadata` verbatim without becoming first-class fields;
  - tool ids with characters incompatible with Python identifiers (hyphens,
    dots) auto-drop the `code_agent` profile from `supported_profiles`;
  - unknown top-level keys raise to catch contract drift early.
- Add external-user examples that demonstrate:
  - agent with no tools;
  - agent with exactly one custom tool;
  - agent with one built-in group plus one custom tool;
  - code-agent profile with only Python-identifier-compatible tools;
  - MCP-imported tools narrowed to an explicit allowlisted subset.
  - backend chat endpoint that streams run events over SSE via the shared runtime
    stream projection.
- Acceptance criteria for this backlog:
  - users can build an agent with an arbitrary selected tool surface without
    constructing `GovernedToolExecutor` directly;
  - users can start with `agent_driver.sdk` defaults while still being able to
    drop down to `agent_driver.runtime` and `agent_driver.tools` when needed;
  - disallowed tools are absent from prompt/tool docs, not only denied at call
    time;
  - existing low-level `ToolRegistry` and policy APIs remain available for
    advanced embedders;
  - tests cover registry filtering, prompt-surface filtering, executor wiring,
    and policy denial as separate behaviors.

Exit criteria:

- high-risk tools can be blocked or interrupted;
- tool outputs are truncated/summarized with metadata;
- tool manifests render deterministic provider-native, ReAct, and CodeAgent-facing docs;
- profile-incompatible tool names/prompts fail validation;
- guardrail decisions are traceable;
- retry of side-effecting tools requires idempotency or explicit policy.
- selected tool sets define both model-visible docs and executable handlers, while
  run policy remains an additional execution-time guard.

## Phase 4: Human-In-The-Loop

- Implement `InterruptRequest`.
- Persist pending interrupt in checkpoint state.
- Implement `ResumeCommand`.
- Support approve/reject/edit/cancel/clarify flows.
- Add UI-facing approval payload shape.
- Add host HTTP-payload normalizers for HITL endpoints:
  - `agent_driver.sdk.resume_command_from_payload(payload, ...)` accepts
    explicit action strings, legacy integer choices (1/2/3), and opaque
    `resume` / `answer` / `value` fields with a customizable
    `value_to_action` resolver; type and invariant errors surface as
    `ValueError` / `TypeError` so the host returns 400 deterministically;
  - `agent_driver.sdk.interrupt_to_stream_event(interrupt, ...)` projects
    an `InterruptRequest` into a transport-neutral dict that hosts wrap
    in their own SSE/WebSocket envelope (e.g. `plan.proposed`);
  - documented in `docs/architecture/hitl-host-mapping.md`.

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
- Add `span_attribute_resolver` hook on optional exporters so host
  applications can attach domain-specific attributes (tenant ids, scan
  profile ids, budget markers, etc.) to each span without subclassing the
  exporter. The resolver receives `(TraceSpan, TraceExport)` and returns a
  `dict[str, str | int | float | bool]`; non-primitive values and
  non-string keys are silently dropped, raising/non-dict returns are
  isolated and reported via `TraceSinkResult.metadata`. Documented in
  `docs/architecture/observability-attribute-hooks.md`.
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
- local in-process CodeAgent executor is intentionally sync-only for callable
  tools: awaitable handlers fail closed and must be mediated via governed
  runtime tool execution rather than direct in-process calls;
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
  - trace events for skipped, successful, and failed compactions
    (emit `RuntimeEventType.MEMORY_COMPACTED` with a stable
    `outcome="skipped"|"successful"|"failed"` plus a
    `compaction_state` snapshot from the orchestrator so hosts can
    bucket Prometheus counters without parsing per-mode payload fields,
    and emit `RuntimeEventType.WARNING` with
    `kind="compaction_circuit_breaker"` /
    `signal_id="compaction_circuit_breaker_open"` on the closed→open
    transition; both projections recognized by
    `agent_driver.adapters.project_warning_event`; documented in
    `docs/architecture/warning-events.md`).

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

Implementation notes from quality-gates pass:

- added deterministic Phase 8 context-quality fixture and retention assertions
  (`tests/context/test_context_quality_eval.py`) covering fact recall, orphan
  action/observation pair checks, provenance coverage, and audit completeness;
- added replay-focused assertions
  (`tests/evals/test_context_quality_replay.py`) to ensure planning/state,
  token-pressure, trim audit, and microcompaction audit remain visible in
  succinct/CLI replay surfaces;
- added provider-neutral strategy comparison baseline report in
  `agent_driver/evals/context_compaction_runner.py` with table output for
  recall/hallucination/provenance/budget and optional latency/cost fields;
- added opt-in OpenRouter live recall lane
  (`tests/runtime/test_live_context_quality_openrouter.py`) that requests strict
  JSON (`remembered`, `missing`, `confidence`) and validates parseability plus
  missing-fact reason semantics.

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

## Phase 10: SDK, Runtime Streaming, MCP, And Product Adapters

- Add app-facing SDK facade on top of runtime/tool primitives:
  - `create_agent(...)`;
  - `Agent.run(...)`;
  - `Agent.stream(...)`;
  - `Agent.resume(...)`;
  - ergonomic provider/store/tool defaults for backend applications.
- Add transport-neutral runtime stream contracts and projection:
  - typed `RunStreamEvent` vocabulary mapped from `RuntimeEvent` and provider
    stream chunks;
  - deterministic event ids/sequence for reconnect-safe consumers;
  - backfill/read-replay path via `RuntimeEventLog.list_for_run(after_seq=...)`;
  - callback hooks and adapter bridges that do not require HTTP coupling.
- Extend runner streaming path:
  - support provider `stream(...)` mode in runtime step flow;
  - emit `TOKEN_DELTA` and related progress events as durable stream events;
  - aggregate final streamed result into standard run output without breaking
    checkpoint/replay semantics.
- Add MCP client design/adapter:
  - import MCP tools into manifest;
  - map MCP `outputSchema` / structured content into `ToolManifest.output_schema`;
  - add MCP security policy controls and descriptor audit metadata.
- Add FastAPI/SSE adapter:
  - async generator/helper API for chat backends;
  - normalized SSE envelope (`event`, `id`, `data`, optional retry metadata);
  - reconnect/backfill behavior from persisted runtime events.
- Add structured warning-event projection for host UIs:
  - emit `RuntimeEventType.WARNING` with a stable `signal_id`, pre-computed
    `severity`, all relevant thresholds, and a derived ratio (currently
    `kind="token_pressure"` covers context-pressure signals);
  - expose `agent_driver.adapters.project_warning_event(stream_event)` as a
    domain-neutral helper that returns a `{kind, signal_id, severity, data}`
    projection or `None` for non-warning / unknown-kind events;
  - keep the human-facing vocabulary (warning ids, copy, suggestions) in the
    host application, not in the runtime;
  - documented in `docs/architecture/warning-events.md`.
- Add CLI adapter baseline with smolagents-style step visibility:
  - `console_scripts` entrypoint and optional `[cli]` extras;
  - commands: `run`, `replay`, `tail`, `tree`;
  - rich live rendering with deterministic plain-text fallback;
  - same stream vocabulary as SSE to avoid duplicated semantics.
- Add example apps:
  - general assistant;
  - codebase assistant;
  - document-analysis assistant;
  - FastAPI chat backend using SSE adapter — see [`examples/chat-demo/`](../examples/chat-demo/README.md).

Exit criteria:

- MCP tools can be allowlisted and approval-gated;
- structured MCP tools preserve output schemas and descriptor audit metadata;
- app-facing SDK can run/stream/resume without manual registry+executor wiring;
- runtime stream contracts are transport-neutral and replayable from durable events;
- streamed runs emit durable `TOKEN_DELTA` events and still produce normal terminal
  `AgentRunOutput` envelopes;
- SSE adapter uses typed runtime stream events with reconnect/backfill tests;
- CLI adapter supports replay and live tail with snapshot-style output tests;
- examples run against fake/local providers;
- low-level `runtime` and `tools` APIs remain available for advanced embedders.

Implementation notes from hardening pass:

- `AgentRunInput` now has explicit `stream: bool` toggle; legacy
  `app_metadata["stream"]` is still accepted for compatibility;
- streaming aggregation moved out of `runtime/single_agent/llm_step.py` into
  `runtime/single_agent/streaming.py`, and token deltas are emitted before
  `llm_call_completed` in deterministic order;
- `RunStreamEvent` now carries explicit stream metadata
  (`schema_version`, `source`, optional `retry_ms`) and projection/backfill
  tests cover lifecycle categories plus in-memory/sqlite backends;
- SDK facade adds typed env config (`SdkConfig`), `Agent.run_text(...)`, and
  resume shortcuts (`approve`, `reject`, `edit`, `cancel`, `clarify`) while
  keeping `agent.runner` as the low-level escape hatch;
- adapters now expose baseline library handlers:
  - SSE: `sse_event_stream(...)` with `Last-Event-ID` parsing and reconnect
    backfill;
  - CLI: deterministic `cli_run_lines`, `cli_replay_lines`, `cli_tail_lines`,
    `cli_tree_lines` over shared `RunStreamEvent` vocabulary.
- CLI UX follow-up now adds optional rich rendering layer:
  - `agent_driver.adapters.cli_rich` provides readable event vocabulary for
    lifecycle/LLM/token/tool/interrupt/warning paths with bounded payload
    previews;
  - rich dependency is optional (`agent-driver[cli]`) and keeps plain-text
    fallback behavior when unavailable;
  - by-eyes smoke script now fails early on missing/invalid credentials and can
    render rich live logs when extra dependency is installed.
- Custom CLI implementation track is now explicitly sliced for delivery:
  - **10.1 installed shell (current MVP):** packaged `agent-driver` command
    with `run`, `replay`, `tail`, `tree` over shared `RunStreamEvent`
    adapters;
  - **10.2 live stream follow-up:** make `Agent.stream(...)` truly incremental
    and add `tail --follow` over durable backfill semantics;
  - **10.3 visual system:** move from line-level formatting to a richer
    terminal UI language with stable plain fallback;
  - **10.4 interactive controls:** expose approve/reject/edit/cancel/clarify
    and run inspection from CLI;
  - **10.5 product parity backlog:** add provider/config/export/doctor
    workflows inspired by OpenClaude boundaries, without porting its Ink stack.
- See [`architecture/custom-cli-roadmap.md`](architecture/custom-cli-roadmap.md)
  for the OpenClaude audit, boundary decisions, and CLI-specific acceptance
  checkpoints.
- Phase 10.2 follow-up now starts landing:
  - `Agent.stream(...)` emits incrementally by polling durable event log while
    run execution is still in progress, instead of waiting for full output;
  - CLI `tail` adds `--follow` mode with polling over `after_seq` semantics and
    terminal-event stop behavior.
- Phase 10.3 foundation now starts landing with terminal chat baseline:
  - new `agent-driver chat` interactive loop over shared runtime stream
    contracts;
  - local slash commands (`/help`, `/exit`, `/clear`, `/runs`, `/replay`,
    `/tail`) for no-model operator actions;
  - chat-oriented rendering that prioritizes assistant token stream with compact
    runtime event notes.
- Phase 10.5 foundation now starts landing with provider wiring:
  - shared provider bootstrap for `run`/`chat` with `fake`,
    `openrouter`, `vllm`, and `ollama`;
  - env and flag based model/base-url/api-key resolution;
  - optional pre-run `--provider-healthcheck` for concise diagnostics.
- Chat tools UX layer now starts landing:
  - shared tool-surface flags for `run`/`chat` and safe default packs;
  - dangerous shell/write packs require explicit opt-in;
  - chat slash command `/tools` (and `verbose`) exposes selected tool surface;
  - renderer suppresses low-value runtime internals like `node_completed`.
- Provider tool-calling bridge now starts landing:
  - OpenAI-compatible requests include selected function-tool schemas;
  - provider normalization maps `message.tool_calls` into `planned_tool_calls`;
  - tool stage can loop back to LLM for follow-up answer after tool execution
    when model finish reason is `tool_calls`.
- CLI productization layer now starts landing:
  - config/profile resolution via `agent-driver config show` and layered defaults;
  - provider diagnostics via `agent-driver doctor` with optional live check;
  - persistent session metadata and session list/show workflows;
  - resume command family (`approve/reject/edit/cancel/clarify`) exposed in CLI and chat slash commands;
  - inspect/export commands for run event artifacts.
- CLI live evaluation and trace audit layer now starts landing:
  - `agent-driver eval run` with opt-in env gate and per-scenario bounded limits;
  - fixed 10-scenario suite with expected/forbidden tool expectations;
  - artifact bundles under `.agent-driver/evals/<timestamp>/` with summary/report/triage;
  - deterministic `agent-driver eval inspect` for summary and compact timeline views.
- Session hardening pass now landed for the chat demo and runtime stream path:
  - chat-demo tool presets split `safe`, `workspace`, `dev`, and `all` so
    default safe mode exposes web/planning tools without filesystem access;
  - per-session workspace metadata, sample import, CLI `--workspace` and chat
    `/workspace` support make filesystem tools operate against explicit scope;
  - shell `cwd` validation now respects the workspace jail in web chat mode;
  - streamed/replayed text-form tool calls are stripped from assistant display
    while raw assistant text is preserved until final sanitization;
  - continuation detection and stronger force-final prompts reduce premature
    final answers that only announce the next step;
  - `todo_write merge=true` accepts status-only rows for existing todo ids;
  - web search/fetch now distinguish zero results, upstream errors, blocked
    pages, and unavailable/time-out resources without forcing false finals;
  - run deadlines interrupt blocking runner steps instead of allowing indefinite
    LLM/tool waits;
  - chat regenerate passes `retry_from_run_id`, truncates persisted transcript
    and `run_ids`, resets local stream state, and avoids stale `Last-Event-ID`;
  - LLM streaming now has an app-configurable idle timeout, avoids retrying a
    provider stream after partial chunks, and handles OpenRouter-style
    `choices: []` bookkeeping chunks;
  - chat stream starts now accept `client_request_id`, reserve a stable run, and
    replay/tail duplicate POSTs without duplicating transcript rows;
  - browser reconnect for chat message streams is enabled only through
    idempotent request identity and durable high-water mark replay, with chat
    runs decoupled from the first HTTP response via a background run task;
  - streaming emits assistant lifecycle snapshots plus tombstone events for
    invalidated partial output, and chat transcript persistence stores only
    finalized assistant text;
  - SSE keepalives are configurable separately from provider stream idle timeout,
    and terminal failures include machine-readable transition reasons such as
    `stream_idle_timeout` and `partial_tombstone`.

OpenClaude-inspired Phase 10 streaming/retry follow-up backlog:

- Consider broker/fanout support if multiple browser clients must attach to the
  same still-running stream across processes.
- Add richer transition reason coverage for continuation nudges and provider
  parser recoveries in observability exporters.

## Deferrals

Do not include in the first implementation:

- scientific paper tools;
- Neo4j/Qdrant assumptions;
- distributed worker backends;
- distributed worker-facing public API adapters for streaming (after SSE baseline);
- production Postgres checkpoint backend is deferred from first cut, but should be prioritized once multi-worker or shared API deployment is required;
- CodeAgent as the default loop or as an unsandboxed executor;
- LangSmith exporter, unless it becomes a target integration;
- complex LLM-as-judge evaluation before deterministic evals exist;
- WebSocket-specific transport layer and broker-specific fanout semantics (until
  HTTP/SSE reconnect baseline is stable);
- highly interactive full-screen TUI beyond rich/plain-text CLI step rendering.

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

## Phase 11: OpenClaude-derived improvements (Landed 2026-05-26)

Context: OpenClaude (https://github.com/Gitlawb/openclaude, MIT) is the
upstream reference for context-engineering and chat-CLI ergonomics that
shaped Phases 6 and 8. After a re-review of openclaude 0.15.0 (the
TypeScript CLI, not the Python SDK), the items below were identified
as additional improvements worth absorbing into agent-driver. None of
them are blockers for downstream consumers; all are backwards
compatible and opt-in.

Downstream context: ZION
(https://gitlab.c.com/batman/red_team_tools) is the primary user of
agent-driver — it pins us by commit ref in `requirements.txt`. ZION's
P5o Priority №4 chat track (and the recon_v3 / Chat v2 timelines)
benefit directly from the items below; see
`docs/design/similar_system_design/unified-plan.md` §3 P3a wave 2 in
that repo for the ZION-side rationale.

**Status (2026-05-26):** All six items landed end-to-end. Test
coverage: 135 cases across H12–H17 + regression on existing executor
suite. Commits (on `main`): `20adfdc` H12, `0cf602c` H16, `2795d6c`
H15, `af94155` H17 (contract), `575fdb4` H13, `66a5e27` H14
(detector + accounting). H14 retry-loop wiring in ``llm_step``
deferred to H14b follow-up — the detector/accounting layer is the
prerequisite and is now in place.

| # | Status | Surface |
|---|--------|---------|
| H12 | **landed** | `ToolManifest.concurrency_safe` + `is_concurrency_safe()` + `agent_driver.tools.executor.partition`; `GovernedToolExecutor` batches via `asyncio.gather` capped by `AGENT_DRIVER_TOOL_CONCURRENCY` (default 8). |
| H13 | **landed** | `AllowedPrompt` + matcher; `InterruptRequest.proposed_prompts`; `ResumeCommand.approved_prompts`; executor consults `AgentRunInput.app_metadata["approved_prompts"]` and collapses INTERRUPT → ALLOW on category match. |
| H14 | **landed** (detector + accounting; retry-loop wiring → H14b follow-up) | `agent_driver.runtime.single_agent.context_window_recovery` with `is_context_window_error()`, `record_reactive_compaction()`, `should_escalate()`, `REACTIVE_COMPACTION_MAX_ATTEMPTS=2`. |
| H15 | **landed** | `agent_driver.contracts.hooks.ToolHook` Protocol + `BaseToolHook`; `GovernedToolExecutor(tool_hooks=[...])`. Pre-hook runs before partition, post-hook after each envelope; chain with per-hook error isolation. |
| H16 | **landed** | `agent_driver.tools.context.ToolProgress` + `report_tool_progress()` + `tool_progress_scope()`; `RuntimeEventType.TOOL_PROGRESS`; `GovernedExecutionResult.progress_events`. |
| H17 | **landed** (contract + resolver; runtime cancel/block wiring → H17b follow-up) | `ToolManifest.interrupt_behavior` + `resolved_interrupt_behavior()`. |

### H12 — Concurrent tool execution partitioning

Reference: `src/services/tools/toolOrchestration.ts:19-82` in openclaude
(`Tool.isConcurrencySafe(input) -> bool` predicate +
`partitionConcurrentTools()` helper).

Today: `GovernedExecutor` calls each tool sequentially. `ToolManifest`
already has `idempotent: bool`; that's a property of the tool, not of
a specific call shape.

Proposal:

- Add `is_concurrency_safe(input) -> bool` callable optional field to
  `ToolManifest` (default: `manifest.idempotent and manifest.side_effect == NONE`).
- Add `agent_driver.tools.executor.partition_concurrent_calls()` that
  splits a batch of tool calls into a sequence of `[parallel_batch, serial_call, parallel_batch, ...]`.
- Executor honors `CONCURRENCY_LIMIT` (default 8) when running parallel
  batches via `asyncio.gather(..., return_exceptions=False)`.
- All errors propagate normally; serial slot resumes after parallel
  batch completes.

Acceptance:

- offline harness benchmark: 10 read-only `file_read` calls finish in
  parallel time, not sum time.
- govern policy guardrail still runs per-call (no batched bypass).

Value/effort: HIGH value (3-5x speedup for read-heavy nodes) / LOW
effort.

### H13 — Prompt-based permissions (allowedPrompts)

Reference: `src/tools/ExitPlanModeTool/ExitPlanModeV2Tool.ts:64-72` in
openclaude (`AllowedPrompt` schema + matcher).

Today: HITL approves each tool call individually after policy match.
Operator fatigue on repetitive auto-approvable categories.

Proposal:

- Extend `InterruptRequest` schema with optional `allowed_prompts: list[AllowedPrompt]`
  field. Operator's `ResumeCommand.action=APPROVE` may include an
  `approved_prompt_ids` list, which the runtime stores in run metadata.
- Add `PromptCategoryMatcher` to `agent_driver.tools.policy`: for
  subsequent tool calls during the same run, matcher evaluates the call
  shape (tool_name + input pattern) against approved categories; on
  match, runtime skips the approval interrupt.
- Plan mode `exit_plan_mode_v2` tool already produces the structured
  approval object — extend it to emit categories in the standardized
  shape.

Acceptance:

- offline test: operator approves `{category: "run tests", patterns: [shell.command: ^npm test]}`;
  three subsequent `shell.command(npm test ...)` calls run without
  interrupt.
- non-matching call (`shell.command(rm -rf /)`) still hits approval.

Value/effort: MEDIUM / MEDIUM.

### H14 — Reactive compaction on max_tokens API errors

Reference: `src/services/compact/compact.ts` in openclaude (reactive
compact path when API returns `max_tokens` / context-window error).

Today: Phase 8 compaction triggers on proactive token-pressure
thresholds. Edge case: tool output spike between two LLM calls can
exceed the window before the next pressure check.

Proposal:

- In `agent_driver.runtime.single_agent.llm_step`, catch
  `MaxTokensExceeded` / `context_length_exceeded` provider errors;
- emit `RuntimeEventType.MEMORY_COMPACTED(reactive=True)`;
- invoke the same layered compaction stack with a stricter target
  (e.g., 50% of window vs. 75% proactive target);
- retry the LLM call once; if still over, escalate to
  `RuntimeEventType.RUN_FAILED(reason="context_window_exhausted")`.

Acceptance:

- offline test: artificial provider error injection triggers reactive
  compaction + successful retry.
- circuit breaker for repeat reactive compactions (max 2 per run).

Value/effort: MEDIUM / MEDIUM.

### H15 — PreToolUse / PostToolUse hooks

Reference: `src/types/hooks.ts` in openclaude (PreToolUse / PostToolUse
/ PermissionRequest hook events).

Today: `GovernedExecutor` runs policy + guardrails (decide-only:
block/sanitize). Cannot modify input or augment output.

Proposal:

- Add `agent_driver.contracts.hooks.ToolHook` Protocol with two
  callables: `pre_tool_use(call) -> ToolCallInvocation | None` and
  `post_tool_use(result) -> ToolCallResult | None`. Return value
  replaces the input/output when not None.
- Hook registration via `Agent` constructor: `tool_hooks=[hook_a, hook_b]`.
- Hooks run in registration order; each hook sees the previous hook's
  output. Errors are isolated per hook (deduplicated `hook_error:<Hook>`
  warning, original input/output preserved).
- Hooks live alongside guardrails but logically distinct: guardrails
  are global policy, hooks are app-specific data transforms.

Acceptance:

- secret-redaction hook test: `pre_tool_use` strips known token
  patterns from `shell.command` input; tool sees redacted input.
- trace-id hook test: `post_tool_use` adds `app_trace_id` to result
  metadata for downstream observability.

Value/effort: MEDIUM / MEDIUM.

### H16 — Tool progress streaming (on_progress callback)

Reference: `src/services/tools/StreamingToolExecutor.ts` in openclaude
(`onProgress` callback yields partial progress messages mid-execution).

Today: Tool.execute is a single async call; output materializes only
on return. Long-running tools (e.g., recon nmap, 15+ min) leave the
operator without feedback.

Proposal:

- Optional `on_progress: ProgressCallback` parameter on
  `Tool.execute(...)`. Tool implementations may invoke it with
  `ToolProgress(kind: str, message: str, completion_ratio: float|None)`.
- Runtime forwards each call as `RuntimeEventType.TOOL_PROGRESS`
  (new) with stable `tool_call_id` correlation.
- `RunStreamEvent` projector emits `tool_progress` envelope alongside
  `token_delta` etc.

Acceptance:

- example/chat-demo: long-running tool renders periodic progress lines
  in the CLI without buffering.
- regression: existing tools without `on_progress` semantics are
  unchanged.

Value/effort: LOW value (UX only) / LOW effort.

### H17 — Tool interrupt_behavior (cancel | block)

Reference: `Tool.interruptBehavior?(): 'cancel' | 'block'` in openclaude.

Today: when a new user message arrives mid-tool-execution, the runtime
queues the message until the tool completes. No per-tool semantic.

Proposal:

- Add optional `interrupt_behavior: Literal["cancel", "block"]` to
  `ToolManifest`. Default for `IRREVERSIBLE` side effects: `"block"`.
  Default for `NONE` / `REVERSIBLE_WRITE`: `"cancel"`.
- Runtime: on incoming user message during tool execution, if
  `interrupt_behavior == "cancel"`, emit
  `RuntimeEventType.TOOL_CALL_COMPLETED(status="cancelled")` and route
  the new message immediately. If `"block"`, queue as today.
- LLM prompt does not need awareness: cancellation surfaces as a
  normal tool result with cancellation status.

Acceptance:

- offline test: read-only tool gets cancelled when a new user message
  arrives; destructive tool blocks.

Value/effort: LOW value (rare path) / LOW effort.

### Phase 11 exit criteria

- H12 + H16 land first (lowest risk, immediate operator UX win).
- H13 + H15 next (semantic features, need ergonomics review).
- H14 + H17 last (edge cases; H14 needs provider error-class survey).
- Each item ships with offline tests + at least one example demonstrating
  the feature in `examples/chat-demo/`.
- No breaking changes to existing public API surface (`Agent.run`,
  `Agent.stream`, `Agent.resume`, `ToolManifest`, `RuntimeEventType`).
- ZION pin bump documents the new opt-in flags in
  `docs/design/similar_system_design/unified-plan.md` §9 history when
  consumed.

## Phase 12: OpenClaude wave 2 — additional patterns (Landed 2026-05-27)

### Phase 12 status table

| Item | Commit | Notes |
|------|--------|-------|
| H18 — Tool output spill-to-disk | `9d0e96c` | `ToolManifest.max_result_size_chars`; executor accepts `artifact_store`; `spill.py` helpers. |
| H19 — Prompt-cache sharing for SubagentGroup | `f76f9bd` | `subagents/cache_safe_params.py`; provider hint helpers for anthropic / openai_compat / vllm / ollama / unknown. |
| H20 — Per-(model, session) cost ledger | `50e28aa` | `observability/cost_ledger.py`; `register_pricing`, `estimate_cost_usd`, `ModelTokenTally`. |
| H21 — Tool dispatch metadata | `b6b0f6d` | `should_defer` / `always_load` / `aliases`; registry alias index + `list_non_deferred()`. |
| H22 — Hook chain aggregation | `9431c80` | `HookResponse` (value, prevent_continuation, additional_context); per-hook `timeout_seconds`. |
| H23 — JSONL session persistence | `55ddad8` | `runtime/storage/jsonl_store.py`; dedup by `event_id`; crash-tolerant read past partial last line. |
| Test suite speedup | `3b48448` | `slow` marker + deselect-by-default; pytest-xdist opt-in. 4+ min → 6s. |



Context: a re-pass through openclaude after closing Phase 11 surfaced
six more patterns with real value. The first six (H12-H17) were the
obvious "Tier 1" items; Phase 12 is the considered Tier 2 — patterns
with clear engineering merit that we chose to defer past the first
batch because of higher effort, narrower applicability, or because
they extend rather than replace existing agent-driver subsystems.

Items NOT included in this Phase (after explicit review):

* PermissionRequest hook variant — H15 + H13 already cover ~95% of
  the realistic use cases; the extra hook surface adds complexity
  without clear new wins.
* CoordinatorMode — openclaude implements it as a system-prompt +
  feature-flag pair, not a framework. Phase 9 SubagentGroup is the
  more general primitive; coordinator patterns belong to app
  layers (ZION recon_v3, examples).
* REPL message queue processor — tightly coupled to Ink/React; not
  applicable to a SDK that doesn't ship its own REPL.
* Streaming token counter stub — deprecated in openclaude.
* Streaming optimizer (collapseReadSearch) — pure UI rendering
  optimization for Ink; not runtime.

### H18 — Tool output spill-to-disk

Reference: openclaude `src/utils/toolResultStorage.ts`,
`Tool.maxResultSizeChars`.

Today: `ToolManifest.output_char_budget` (default 4000 chars) triggers
``enforce_output_budget`` which truncates the summary mid-string. For
large structured payloads, ``_bounded_structured_output`` also caps
list lengths and marks ``truncated=True``. The lost data is unrecoverable
within the run.

Proposal:

- Add `ToolManifest.max_result_size_chars: int | None = None` (default
  ``None`` → use a global 50 KB cap; explicit ``None`` semantically
  same; explicit value overrides; ``math.inf``-style "never spill"
  via a sentinel ``float('inf')`` or large int).
- When raw handler output exceeds the cap, write the full payload to
  `agent_driver.context.artifacts` (already Phase 6 surface) and
  replace ``raw`` in the envelope with a structured wrapper:

  ```python
  {"summary": "<2KB preview>",
   "persisted_artifact": {"name": "<artifact_id>",
                          "size": <bytes>,
                          "mime": "application/json"},
   "truncated": False,  # not lost — persisted
   "persisted": True}
  ```

- LLM observation includes a ``<persisted-output>`` tag with the
  preview + artifact ref so the model can decide to read the artifact
  via a follow-up tool call (existing ``read_artifact`` already in
  builtins).
- File-read style tools opt out via large ``max_result_size_chars``
  because their output IS the data the model needs in-context.

Acceptance:

- offline test: a tool that returns 200 KB JSON gets persisted; the
  envelope carries preview + artifact_ref; ``read_artifact(name)``
  returns the original payload byte-identical.
- regression: tools that fit in budget produce identical envelopes
  to today (no behaviour change).

Value/effort: HIGH / MEDIUM. Direct context-window save for recon /
file-grep heavy runs.

### H19 — Prompt-cache sharing across SubagentGroup children

Reference: openclaude `src/utils/forkedAgent.ts` — `CacheSafeParams`
+ `lastCacheSafeParams` singleton.

Today: ``execute_subagent_group_sync`` spawns each child with its own
``AgentRunInput``, independently constructed. Each child triggers a
fresh provider request whose prompt prefix (system + tools + parent
message preamble) the provider treats as cold cache — billing twice
(or 4× for a 4-way fan-out).

Proposal:

- Add `agent_driver.subagents.cache_safe_params.CacheSafeParams`
  dataclass — immutable struct containing `(system_prompt, tools,
  model, parent_prefix_messages)`. Subagents that share this struct
  are guaranteed cache-eligible at the provider layer.
- `execute_subagent_group_sync` derives a `CacheSafeParams` from the
  parent run; each child's `AgentRunInput` references it
  (by-reference, not by-copy).
- Add a provider-aware caching layer:
  - Anthropic: emit `cache_control: {"type": "ephemeral"}` markers
    on the shared prefix in the request payload.
  - OpenAI compatible: extra_body hint (`prompt_cache=true`) when
    the provider advertises support (vLLM ≥ 0.5, some
    Together/Groq deployments).
  - Other providers: no-op (still share the params by reference,
    just don't claim cache support).
- Mutable per-child state (sub-agent's own message buffer,
  workspace cwd, abortable token) stays per-child — only immutable
  state shares.

Acceptance:

- offline test: a 4-way SubagentGroup shares the same parent
  prefix; provider mock asserts identical prompt prefix bytes
  across all 4 calls.
- integration test (Anthropic): a 4-way fan-out reports
  ``usage.cache_read_input_tokens > 0`` on calls 2/3/4.

Value/effort: HIGH / HIGH. Direct $ savings for parallel sub-agent
fan-outs.

### H20 — Per-(model, session) cost ledger

Reference: openclaude `src/cost-tracker.ts` + session config.

Today: ``LlmResponse.usage`` carries per-call tokens; observability
exporters (Phoenix, Langfuse) get per-call traces. There's no per-run
or per-session cost rollup.

Proposal:

- `agent_driver.observability.cost_ledger` — new module with
  `CostLedger` dataclass:

  ```python
  @dataclass
  class CostLedger:
      per_model: dict[str, ModelTokenTally]
      per_tool_duration_ms: dict[str, float]
      lines_added: int = 0
      lines_removed: int = 0
      total_api_duration_ms: float = 0.0
      total_api_duration_ms_incl_retries: float = 0.0
  ```

- Hook into the runtime's existing ``LLM_CALL_COMPLETED`` event
  payload: accumulate tokens + USD (lookup by canonical model id
  from a small pricing table in `agent_driver.observability.pricing`).
- Persist ledger snapshots to the checkpoint store under
  `cost_ledger_v1` key; `/resume` re-hydrates.
- New `RuntimeEventType.COST_LEDGER_UPDATED` event for downstream
  consumers (ZION report builder pulls these to put "cost: $X.XX"
  in the DOCX summary).

Acceptance:

- offline test: 3-step run with mock provider returning usage →
  ledger reflects correct cumulative tokens + USD.
- /resume restores ledger from checkpoint; subsequent steps
  accumulate on top.

Value/effort: MODERATE / MEDIUM.

### H21 — Tool metadata for dispatch (defer / always_load / aliases)

Reference: openclaude `Tool.ts` + `toolSearch.ts`.

Today: ``ToolRegistry`` always emits the full registered set in the
agent's initial system prompt. With > 100 tools, this consumes
significant tokens.

Proposal:

- Add 3 ``ToolManifest`` fields:
  - `should_defer: bool = False` — when ``True``, tool is omitted from
    the initial prompt; only inserted after a ``tool_search`` call
    returns it.
  - `always_load: bool = False` — explicit opt-out from deference
    (e.g. system tools like ``ask_user_question``).
  - `aliases: list[str] = []` — alternative names; registry lookup
    checks both primary and aliases for backwards compat after
    renames.
- ``ToolSet.from_preset`` and SDK rendering paths honor ``should_defer``
  unless an explicit env var (``AGENT_DRIVER_TOOL_SEARCH_MODE=eager``)
  flips to eager mode.
- ``tool_search`` builtin (already in registry as ``catalog_search``)
  returns deferred tools by name + manifest snippet; the LLM then
  invokes them; registry promotes them to "loaded" for the rest of
  the run.

Acceptance:

- offline test: registry with 5 deferred + 2 always_load tools →
  initial prompt enumerates only the 2; after ``tool_search``
  matching one deferred tool, next prompt includes it.
- alias lookup test: registering ``file_read`` with
  ``aliases=["read_file"]`` makes both names resolve.

Value/effort: MODERATE / LOW. Becomes important when ZION grows
its tool catalog past ~100 tools.

### H22 — Hook chain aggregation

Reference: openclaude `src/utils/hookChains.ts`.

Today (Phase 11 H15): ``GovernedToolExecutor(tool_hooks=[...])`` runs
hooks in order; each hook sees previous output (chain). Hook errors
are isolated per-hook. There's no aggregation of permission decisions,
no "first blocker wins" early-exit semantic beyond exceptions, and
no async hook with external-approver timeout.

Proposal:

- Extend ``ToolHook`` Protocol with optional ``preventContinuation:
  bool`` flag on hook response. When True, the runtime exits the
  chain early (other hooks for that event skipped) and applies the
  decision.
- Add async hook support: hooks may return a coroutine; runtime
  awaits with per-hook timeout (configurable via
  ``ToolHook.timeout_seconds``).
- ``HookChainResult`` aggregates per-hook outputs: first-blocking
  flag, merged permissions, merged additional_context.

Acceptance:

- offline test: 3-hook chain where hook 2 returns
  ``preventContinuation=True`` → hook 3 not called; decision applied.
- async hook with 100 ms timeout returns within budget → applied;
  one that exceeds timeout → ignored with warning.

Value/effort: MODERATE / MEDIUM. Important for plugin systems
(multi-vendor security overlays).

### H23 — JSONL session persistence + batched flush

Reference: openclaude `src/utils/sessionStorage.ts` +
`sessionRestore.ts`.

Today: agent-driver has SQLite/Postgres ``RuntimeEventLog`` +
``CheckpointStore``. The pattern works but requires DB setup.

Proposal:

- ``agent_driver.runtime.storage.jsonl_store.JsonlRuntimeStore`` —
  new backend implementing the existing protocols.
- One file per session: ``{storage_dir}/{session_id}.jsonl``.
- Per-file ``asyncio.Queue`` + 100 ms batched flush; dedup by
  ``event_id`` before append (so retries don't double-write).
- ``parent_event_id`` chain reconstruction on resume (linked-list
  style; tolerates out-of-order writes when reading).
- Tail-scan optimization: ``read_metadata(session_id, limit=N)``
  reads the LAST N lines via reverse-seek without loading the full
  file — useful for ``--list-sessions`` / restoration UIs.

Acceptance:

- offline conformance: same replay/resume test suite as SQLite
  passes against JSONL backend.
- 10k-event write smoke: batched flush coalesces into < 100
  syscalls.
- corruption test: a truncated JSONL line at EOF is ignored on
  restore (don't crash on partial writes from a kill -9).

Value/effort: MODERATE / MEDIUM. Cheap durable tier for
single-user / CLI workflows; ZION fallback when Mongo unavailable.

### Phase 12 sequencing recommendation

1. **H21 + H18** first (LOW/MEDIUM effort, immediate value).
2. **H22** next (enhancement of H15 hook chain; minimal new contracts).
3. **H20 + H23** then (observability + persistence; somewhat
   independent of LLM-step path).
4. **H19** last (HIGH effort, provider-aware code; touches Phase 9
   subagent executor).

### Phase 12 exit criteria

- Same as Phase 11: offline tests + no breaking changes to public
  surface + opt-in flags documented in ZION ``unified-plan.md``
  on each pin bump.
- Additionally: each item updates the existing examples
  (``examples/chat-demo``, ``examples/eval``) where the feature is
  observable.


## Phase 13: provider hardening — production resilience (In progress 2026-05-27)

**Status snapshot (2026-05-27, functionally closed):** H24, H25, H26,
H28, H29.1, H29.2, H29.3 all landed. Only H27 (vLLM guided decoding)
remains, deferred until ZION vLLM deploy gates unblock — out of scope
for this wave. Wave was brought up live by ZION live-verify session
that exposed: original 503 cascade (H25 motivation), operator_report
JSON-tail flake (H26 motivation), operator UI flicker (H28
motivation), and tool-calling robustness gaps for open-weights models
(H29 family motivation).

| # | Status | Commit | Surface |
|---|--------|--------|---------|
| H24 | **landed** | `5657632` | Anthropic prompt-cache via cache_control ephemeral; `LlmRequest.enable_prompt_cache` opt-in. |
| H25 | **landed** | `9cade72` | OpenAI-compat 429/5xx retry w/ Retry-After honor; base.py retry loop wrapper. |
| H26 | **landed** | `a9e93f5` | `LlmRequest.response_format` passthrough on OpenAI-compat; validator for json_object / json_schema shapes; vendor `extra_body` overrides preserved. ZION Tier 2 entry point for slice 4.L follow-up — once pinned, ZION's `instructor_findings_extractor` switches from direct openai client to agent-driver-routed call so H4 (span attrs) / H20 (cost ledger) / Phase 8 (compaction) integrate. AnthropicMessagesProvider silently drops the field today (no native equivalent API key); follow-up can translate to system-prompt addendum + post-call validate. |
| H27 | planned | — | vLLM guided decoding (`guided_json` / `guided_regex` / `guided_choice`); deferred until ZION vLLM deploy is unblocked. |
| H28 | **landed** | `a2c1508` | `agent_driver.llm.streaming_optimizer.coalesce_stream` standalone async-generator helper. Window (default 80ms) + idle (default 200ms) flush; non-delta events flush pending buffer THEN pass through verbatim; reasoning channel preserved without coalescing; producer exceptions drain buffer first. Caller opts in by wrapping their provider stream. |
| H29.1 | **landed** | `16b67dc` | Explicit `parallel_tool_calls: bool \| None` on OpenAI-compat; null = backend default. |
| H29.2 | **landed** | `3cd1641` | Tool result image attachment unpacking. New module `agent_driver.llm.tool_result_unpacker` extracts `attachments: [{kind, mime_type, data}]` from tool envelope's `structured_output`, plants them on `ChatMessage.metadata`; OpenAI-compat `_payload` emits content-list shape with text + image_url blocks. Tools producing screenshots / OCR images now reach the model as actual visual input instead of mangled string-coerced bytes. Anthropic native shape follow-up deferred. |
| H29.3 | **landed** | `c92e7a1` | Tool-call fallback feedback. New module `agent_driver.tools.fallback_feedback` with `closest_tool_names` (difflib fuzzy match), `build_unknown_tool_feedback`, `build_arguments_parse_feedback`, `build_missing_tool_name_feedback`. `AllowedSpec` gains `available_tool_names`; `tool_not_registered` block path uses fuzzy match → "Did you mean: X?" feedback. Open-weights models recover in one turn instead of looping. The parse-error feedback helpers are landed but not yet wired (separate slice). |

---


Context: Phase 1.2 root-cause investigation of the ZION P5o slice 4.L
operator_report JSON-tail bug exposed a side-by-side comparison of
agent-driver vs openclaude provider implementations. agent-driver has
solid baseline tool-calling for Anthropic + OpenAI-compatible
providers, but lacks several production-resilience features that
openclaude has. ZION's litellm.c.com 503 cascade during validation
run `d9fa88f3` (Qwen3.6 cold-start + auth glitch killed mid-run
without retry) is a concrete operator pain that motivates this wave.

Five items, sequenced by effort × impact. Recommended order:
**H24 + H25 first** (LOW/MED effort, immediate value), then H26,
H28, H27 if vLLM deploy gets prioritized.

### H24 — Anthropic prompt-cache (cache_control ephemeral)

Reference: openclaude `src/utils/api.ts` (cache_control ephemeral
blocks on tools and large system prompts).

Today: `AnthropicMessagesProvider` (~450 LOC) emits raw
`tools=[{name, description, input_schema}]` and `system="..."`
without cache_control markers. Every request re-bills the full
prompt prefix (system + tools catalog) — typical recon-v3
operator_report has ~3k tokens of system + tools = ~3k tokens
billed at full input rate per call.

Proposal: when the parent run signals a cacheable prefix (via
`CacheSafeParams` from H19, or an explicit per-call flag), the
provider attaches `cache_control: {type: "ephemeral"}` to:
- the `system` field (as a content block with cache_control), and
- the LAST tool in the `tools` array (Anthropic caches everything
  up to and including the marker — see Anthropic docs).

Acceptance:
- offline test: a request with two consecutive calls to the same
  prefix produces `cache_read_input_tokens > 0` on call 2 (against
  FakeProvider that simulates the field);
- contract test: `cache_control` markers are NEVER set when the
  caller didn't request caching (avoid bloating tokens for one-off
  calls).

Value/effort: HIGH (cost win) / LOW (~50 LOC + 4 tests).

### H25 — OpenAI-compatible 429 retry + Retry-After honor

Reference: openclaude `src/services/api/openaiShim.ts:103-105 +
2311-2326` (GitHub Models specific: 1→2→4→32s exp backoff capped,
Retry-After header parsed).

Today: `OpenAICompatibleProvider` retries once on stream-open
failure (`base.py:138-153`) but has NO 429 / 503 / 502 retry loop.
ZION's recon_v3 run `d9fa88f3` died mid-flight when litellm.c.com
returned 503 "Loading model" three times consecutively — no fallback
was attempted, and the model would have been warm by retry 2.

Proposal: generic retry loop on the OpenAI-compatible provider for:
- 429 Too Many Requests — honor `Retry-After` header if present,
  else exponential backoff (1s, 2s, 4s, 8s, 16s, 32s, capped at
  3 retries).
- 503 Service Unavailable — same backoff, suggests transient.
- 502 Bad Gateway — same backoff, capped at 2 retries.
- 5xx other — same backoff.
Network errors (DNS / TLS handshake fail / connection reset) are
already partly handled in `base.py` stream-open retry; this slice
ONLY adds the HTTP-status-code branch.

Acceptance:
- offline test: mocked 429 with Retry-After: 1 → request succeeds
  on retry 2 after ~1s delay;
- offline test: three 503s in a row → request retries 3 times then
  raises;
- contract: streaming requests get the same retry on the OPEN; mid-
  stream chunks don't retry (those are unrecoverable).

Value/effort: HIGH (reliability) / MEDIUM (~80 LOC + 6 tests).

### H26 — OpenAI `response_format=json_schema` (decode-time enforcement)

Reference: openclaude `src/services/api/codexShim.ts:380-432`
(`enforceStrictSchema`: strips `uri` format, sets
`additionalProperties=false`, recurses nested objects).

Today: `OpenAICompatibleProvider` accepts `response_format` only
when callers pass it via `Config.extra_body`. There's no first-
class API to request `{"type": "json_object"}` or
`{"type": "json_schema", "json_schema": {...}}` from the SDK
contract. Modern OpenRouter (Qwen, GPT-4) and OpenAI itself
support decode-time JSON schema validation: the model is FORCED to
produce a response matching the schema. This would be a much
stronger fix for the ZION 4.L JSON-tail bug than prompt-engineering
— but it requires the response to be PURE JSON (no surrounding
markdown), so adopting it means restructuring operator_report.

Proposal: add `LlmRequest.response_format` field (Pydantic):

  ```python
  class ResponseFormatJsonObject(BaseModel):
      type: Literal["json_object"] = "json_object"

  class ResponseFormatJsonSchema(BaseModel):
      type: Literal["json_schema"] = "json_schema"
      json_schema: dict[str, Any]
      strict: bool = True

  ResponseFormat = ResponseFormatJsonObject | ResponseFormatJsonSchema | None
  ```

Then `OpenAICompatibleProvider` translates to the wire request
body, applying `enforceStrictSchema` projection (mirror openclaude).
`AnthropicMessagesProvider` has no native support — falls back to
adding a strong "respond with JSON matching this schema" system-
prompt addendum + post-call validation.

Acceptance:
- offline test: round-trip a schema through `response_format` →
  wire body has the right shape;
- contract test: `json_schema` strict=True coerces `additionalProperties=False`
  at all nesting levels;
- compat test: Anthropic provider receives the same request and
  doesn't crash (graceful degradation via system prompt addendum).

Value/effort: MEDIUM (proper structured-output) / MEDIUM (~80 LOC + 5 tests).

### H27 — vLLM guided decoding (guided_json / guided_regex / guided_choice)

Reference: openclaude has no vLLM-specific support either. This is
a new feature in agent-driver.

Today: ZION's vLLM deploy is on the Phase 3+ roadmap. When it
lands, deterministic structured output via vLLM's guided decoding
would be the gold standard — the decode loop is constrained to
only emit tokens that match the JSON schema / regex / choice list,
making model output FORMALLY guaranteed to validate.

Proposal: when `provider.kind == "vllm"` (or via opt-in flag),
translate `response_format=json_schema` to vLLM's `extra_body`:

  ```python
  extra_body = {
      "guided_json": json_schema,
      # or
      "guided_regex": pattern,
      # or
      "guided_choice": ["one_of", "these", "tokens"],
  }
  ```

Acceptance:
- offline test against a vLLM mock: schema in `response_format` →
  request body has `extra_body.guided_json` populated;
- conformance: when both `response_format` and `extra_body.guided_*`
  are set, `extra_body.guided_*` wins (operator override);
- contract: non-vLLM providers ignore the vLLM-specific knobs.

Value/effort: HIGH (vLLM gold standard) / HIGH (~200 LOC + integration
test against a running vLLM instance).

### H28 — Streaming optimizer (buffer + flush + chunk coalescing)

Reference: openclaude `src/utils/streamingOptimizer.ts`
(`createStreamState`, `processStreamChunk` — accumulates tokens
into ~80ms buffers, emits coalesced chunks, smooths perceived
latency for chat UIs).

Today: `OpenAICompatibleProvider` streams line-by-line.
`AnthropicMessagesProvider` streams per-event. Both emit raw
SSE events as the SDK consumer's stream. ZION chat UI flickers
on rapid delta arrivals (visible during recon_v3 progress; also
the operator's #1 observation in `my-findings-about-last.md`:
"Runs page обновляется каждые 5 секунд").

Proposal: add `agent_driver.llm.streaming_optimizer` with
`StreamCoalescer` — receives raw stream events, batches `text_delta`
into ~80ms windows, flushes on `tool_use_start` / `tool_use_stop`
/ `message_stop` / 200ms idle. Caller opts in via a request flag.

Acceptance:
- offline test: 100 1-byte deltas in 50ms → 1 coalesced chunk
  emitted;
- contract: tool-use start/stop events are NEVER coalesced (UI
  needs them prompt);
- perf: typical recon_v3 progress stream cuts emitted chunks by
  ≥3× without losing information.

Value/effort: MEDIUM (UI smoothness) / MEDIUM (~100 LOC + 8 tests).

### Phase 13 sequencing recommendation

1. **H24 + H25** first — LOW + MED effort, both immediate value
   (cost / reliability). Each independent; can land in parallel.
2. **H26** next — MEDIUM effort, opens the door for principled
   structured output (downstream candidate fix for ZION 4.L JSON
   tail bug if operator_report gets restructured to emit pure
   JSON).
3. **H28** then — MEDIUM effort, UI quality win.
4. **H27** last — HIGH effort, deferred until vLLM deploy gates
   are unblocked.

### Phase 13 exit criteria

- Same as Phase 12: offline tests + no breaking changes to public
  surface + opt-in flags documented in ZION `unified-plan.md`.
- Each item adds at least one regression test demonstrating the
  resilience / cost-saving / structured-output behavior.
- ZION's recon_v3 stack picks up the new providers via the pin
  bump after each commit.
