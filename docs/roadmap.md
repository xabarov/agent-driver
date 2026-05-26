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
