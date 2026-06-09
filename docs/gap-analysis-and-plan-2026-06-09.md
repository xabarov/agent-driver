# Gap Analysis And Horizontal Work Plan

Status: active plan / new horizontal tracks.

Date: 2026-06-09.

Purpose: complement the artifact-first Deep Research sequence in
[Unified work plan](unified-work-plan-2026-05-31.md) with the
*horizontal* runtime gaps that keep `agent-driver` from being a general
runtime for agentic applications (the README promise), not just a Deep
Research vertical.

This plan was produced after a deep comparison against two reference
runtimes:

- **NousResearch/hermes-agent** — mature production agent (cron, gateway,
  pluggable long-term memory, rich provider error taxonomy, MCP server,
  batch trajectory generation).
- **Gitlawb/openclaude** — TS Claude Code alternative (hook-chains for
  self-healing, descriptor-first multi-provider routing, layered
  compaction, granular permission model).

## What agent-driver already has (verified in code)

The single-process, single-user agent core is effectively closed:

- durable runtime: memory / sqlite / postgres checkpoints, event log,
  replay, resume;
- governed tools: registry, manifests, risk / side-effect policy,
  guardrails pipeline, `tool_gate`, `tool_search`;
- context compaction: `context/compaction`, microcompaction, token-pressure
  ladder;
- subagents: spawn, durable child rows, mailbox handoff, join, merge,
  synthesis;
- planning (live todos vs modal approval), steering via a control queue;
- health-aware provider router with fallback;
- OpenInference / Phoenix tracing, per-(model, session) cost ledger;
- Skills with a curated research set; Deep Research contracts;
- SDK facade (`create_agent`, `query`, `Session`, `RunHandle`, streaming).

## Gaps (ranked)

Severity is for "general agentic runtime", not for the Deep Research
vertical.

| # | Gap | Current state | Reference |
|---|-----|---------------|-----------|
| 3 | Provider error taxonomy + smart failover | router only marks unhealthy and fails over on any `RuntimeError`/`ValueError`; no reason-aware action | hermes `error_classifier` |
| 2 | Long-term cross-session memory | `contracts/memory.py` is only an event→context projection; no recall/prefetch/store provider | hermes `plugins/memory/` |
| 1 | Real scheduler/cron executor | `tools/builtin/automation.py` cron_*/remote_trigger/push are **session-local intent records** only — no backend that runs them | hermes `cron/` |
| 4 | MCP server (expose agent-driver itself) | only an MCP *client* (`tools/builtin/mcp.py`) | hermes `mcp_serve.py` |
| 5 | Hook-chains for self-healing | `hook_chains` primitives exist, no recovery recipes (spawn fallback, cooldown, depth guard) | openclaude hook-chains |
| 6 | Headless / gateway server | only CLI + chat-demo; no gRPC/SSE server with `action_required` approvals or platform delivery | hermes gateway / openclaude gRPC |
| 7 | Granular permission model | guardrails by risk/side-effect; no dangerous-shell classifier or deny-rules | openclaude permissions |
| 8 | Descriptor-first multi-provider | providers wired in code; no metadata/routing/transport separation | openclaude `integrations/` |
| 9 | Batch runner / trajectory generation | none | hermes `batch_runner.py` |
| 10 | More worked examples | only `examples/chat-demo` | both |

Lower priority but cheap wins: a single slash-command registry as the
source of truth; deferred cache-invalidation policy for state-mutating
commands (protect prompt cache); ACP adapter for IDE integration.

## Track 0 — Architecture Hardening (DONE 2026-06-09)

Delivered, suite green throughout (no regressions; 13 failures present before
and after are all pre-existing):

- **A5** — `RunnerConfig.with_overrides()` shallow override-copy replaced
  `deepcopy(config)` in `create_agent`; stateful deps (memory DB
  connection/lock) are now safe to attach.
- **A4** — `MemoryRuntimeState` (`_MetadataView`) owns `recalled_memory` /
  `memory_synced`; Track B no longer touches raw `context.metadata` keys.
- **A1** — `runtime/lifecycle_hooks.py` adds `RunLifecycleHook`
  (`on_run_start`/`on_finalize`) + `dispatch_*`; the step loop dispatches
  generically and long-term memory is now a `MemoryLifecycleHook`, not
  hardcoded calls in `steps.py`. `create_agent`/`query` take `lifecycle_hooks`.
  This is the capability-registration seam future tracks reuse (scheduler,
  permissions, telemetry, hook-chains `on_error`).
- **A2** — deep-research policy moved to
  `runtime/single_agent/research/gating.py`; `lifecycle/steps.py` shrank from
  1242 → 264 lines and deep-research mentions from 143 → 3 (a lean generic
  driver). The two entry points stay importable from `lifecycle.steps`.
- **A3** — dropped `slots=True` from `RunnerConfig`, removing the
  dual-declaration foot-gun (adding a field is now one assignment); added a
  construction guard test. The behavioral-capability registry need is met by
  A1's hook seam, so no separate registry was built.

Original analysis and rationale retained below for the record.

## Track 0 — Architecture Hardening (analysis; do before #1, #4–#10)

Purpose: implementing Tracks A and B surfaced concrete growth-taxes in the
runtime. Each remaining horizontal gap (#1 scheduler, #4 MCP server, #5
hook-chains, #7 permissions, #8 descriptor providers) pays these taxes again
unless we pay them down once. This track is a deliberate, time-boxed detour
that makes every later track cheaper; it changes structure, not behavior, and
must keep the suite green at each step.

Observed friction (with evidence):

- No run/turn lifecycle seam. The single-agent loop invokes **zero**
  lifecycle hooks; `contracts/hooks.py` only defines `ToolHook` (pre/post
  tool, dispatched solely in `tools/executor/governed.py`). To add memory
  (Track B) we hardcoded prefetch/sync into `lifecycle/steps.py`.
- `lifecycle/steps.py` is a 1242-line "generic" module with **143
  deep-research mentions**; the generic step handlers are ~200 lines and the
  rest is research-specific gating/repair. Any lifecycle change wades through
  research code.
- Runtime state is largely untyped `context.metadata["..."]` magic strings.
  An inventory test already flags 23 undocumented keys; Track B added 2 more
  raw keys instead of a typed owner, even though `metadata_state.py` has the
  `_MetadataView` pattern (`LoopControlState`, `ResearchRuntimeState`, …).
- `RunnerConfig` is a hand-rolled `__init__(**kwargs)` + `slots=True`; adding
  one field needs synchronized edits in the slot annotation, `__init__`,
  `RunnerDeps`, and the runner — and a missed slot raised `AttributeError`
  mid-implementation. `RunnerDeps` is a flat field list that grows linearly
  per capability.
- `create_agent` does `deepcopy(config)`, which cannot copy a stateful
  component (the memory provider's SQLite connection/lock); we needed a
  detach/restore hack.

Work items (each independently shippable; keep tests green):

- **A1 — Run/turn lifecycle hook seam.** Add a `RunLifecycleHook` protocol
  (`pre_run`, `pre_llm`/`post_llm`, `post_turn`/`on_finalize`, `on_error`) and
  dispatch points in the step loop; carry hooks on deps. Migrate the Track B
  memory prefetch/sync to be the first consumer (removes the hardcoded calls).
  Unblocks: #1 (scheduler hooks turn boundaries), #5 (wire
  `HookChainExecutor.observe` at `on_error`), #7 (permission/audit hook),
  telemetry.
- **A2 — Extract deep-research policy out of the generic loop.** Move the
  `_deep_research_*` / `_force_research_repair_*` helpers from `steps.py` into
  `runtime/single_agent/research/`, consumed via the A1 seam / existing
  contract. Leaves a lean generic driver and makes deep research a pluggable
  profile policy (the hermes "profiles" model). Unblocks every lifecycle-touch
  track and future agent profiles.
- **A3 — Typed config + capability registry.** Replace the `kwargs`/`slots`
  `RunnerConfig` with a typed config and introduce a small capability registry
  so new subsystems (memory, scheduler, permission policy, descriptor
  providers) register instead of threading flat `RunnerDeps` fields end to end.
- **A4 — Typed state owner for new keys.** Add `MemoryRuntimeState`
  (`_MetadataView`) and route `recalled_memory`/`memory_synced` through it
  (retrofit Track B). Rule going forward: new runtime state uses a typed
  owner, not raw `context.metadata[...]`.
- **A5 — Remove `deepcopy(config)` fragility.** Replace whole-config deepcopy
  in `create_agent` with explicit override-copy (e.g. `config.with_overrides`)
  so stateful components are safe to attach. Low effort, removes a recurring
  trap as more stateful deps land.

Suggested order (cheap/isolated → structural): A5 → A4 → A1 → A2 → A3.
A1 and A3 may swap if we prefer hooks to register through the new registry
from the start. A2 is the largest and riskiest; gate it behind A1 landing.

Acceptance: suite stays green throughout; Track B's hardcoded memory calls in
`steps.py` are gone (moved behind A1); `steps.py` no longer contains
research-specific helpers; adding a new runtime capability touches one
registration site, not four; no new raw `context.metadata` keys for memory.

Mainline impact: after Track 0, resume the horizontal sequence (#1 scheduler,
then #4–#10) on the cleaner seams. Track 0 is structural debt paydown, not a
new feature; if any item over-runs, ship the landed items and defer the rest
with a dated note here.

## Track A (active) — Provider Error Taxonomy + Smart Failover (gap #3)

Goal: the router/runtime acts on *why* a provider failed instead of
treating every failure as "mark unhealthy, try the next provider".

Context: transport-level retries for 429/502/503/504 with `Retry-After`
already exist in `ProviderBase`. The missing layer is *semantic*: a
classified reason that drives the router and runtime decision.

Steps:

- Add `llm/error_classifier.py`:
  - `ProviderErrorReason` enum: `AUTH`, `BILLING`, `RATE_LIMIT`,
    `OVERLOADED`, `SERVER_ERROR`, `TIMEOUT`, `CONTEXT_OVERFLOW`,
    `PAYLOAD_TOO_LARGE`, `MODEL_NOT_FOUND`, `CONTENT_POLICY`,
    `PROVIDER_POLICY`, `FORMAT_ERROR`, `TRANSPORT`, `UNKNOWN`.
  - `RecoveryAction` enum: `RETRY_SAME`, `BACKOFF_RETRY`,
    `ROTATE_PROVIDER`, `COMPRESS_CONTEXT`, `FAIL_FAST`.
  - `ClassifiedError` dataclass: `reason`, `action`, `retryable`,
    `provider`, `status_code`, `request_id`, `message`.
  - `classify(exc) -> ClassifiedError`: map the existing
    `ProviderStatusError` / `ProviderTimeoutError` / `ProviderTransportError`
    and raw `httpx` causes by status code + message heuristics.
- Wire `HealthAwareRouter`:
  - consult `classify()` in `complete()` / `stream()`;
  - `FAIL_FAST` reasons (`AUTH`, `BILLING`, `CONTENT_POLICY`,
    `MODEL_NOT_FOUND`, `PAYLOAD_TOO_LARGE`) → do **not** rotate, raise
    immediately (deterministic per request);
  - `CONTEXT_OVERFLOW` → raise a distinct signal so the runtime can
    compress and retry rather than blindly failing over;
  - `BACKOFF_RETRY` / `OVERLOADED` / `RATE_LIMIT` → bounded backoff before
    rotating;
  - only `TRANSPORT` / `SERVER_ERROR` / `TIMEOUT` mark a provider
    unhealthy.
- Surface the classified reason on the runtime stream/error events and in
  trace metadata.

Acceptance:

- Deterministic unit tests: each reason maps to the documented action;
  the router rotates only on rotate-eligible reasons and fails fast on
  auth/policy/overflow.
- A simulated `CONTEXT_OVERFLOW` triggers a compress-and-retry path, not a
  provider rotation.
- No behavior change for the already-handled transient 429/5xx transport
  retries.

## Track B (done) — Long-Term Cross-Session Memory (gap #2)

Optional pluggable memory so multi-session agents can recall prior context.
Implemented in `agent_driver/memory/`:

- `MemoryProvider` ABC (`prefetch`, `sync_turn`, `shutdown`, `post_setup`)
  plus a default `StoreBackedMemoryProvider` doing recency + keyword recall.
- `MemoryStore` protocol with `InMemoryMemoryStore` and durable
  `SqliteMemoryStore` backends; external providers can implement the same
  protocol out of tree.
- Runtime wiring: prefetch once at `run_started` into
  `context.metadata['recalled_memory']`, injected as a filter-safe block in
  `react_system_instruction` (background context, not instructions), and a
  one-time `sync_turn` at terminal `finalize` (guarded by `memory_synced`).
- SDK surface: `create_agent(..., memory_provider=...)` and
  `query(..., memory_provider=...)`; the provider is carried past the config
  deepcopy by reference so DB connections/locks are not copied.

Acceptance met: a fresh agent over the same SQLite store recalls a fact
written by an earlier agent instance for the same session; recall is bounded
(`render_recall_block` char cap + query limit) and sessions are isolated.

Follow-ups (not blocking): semantic/embedding recall as an alternate store;
extraction/distillation instead of raw turn text; memory-aware compaction so
recalled facts survive context compaction.

## Track C (DONE 2026-06-09) — Real Scheduler For Existing Cron Intents (gap #1)

Turned the inert session-local `cron_*` intent stubs into a working durable
scheduler. New `agent_driver/scheduler/` package:

- **C1** `schedule.py` — dependency-free `Schedule.parse` for 5-field cron
  (`*`, lists, ranges, steps), interval shorthand (`every 5m/2h/1d`) and
  `@hourly`/`@daily`/`@weekly`/… macros; deterministic `next_after(dt)` with
  Vixie DOM/DOW OR semantics.
- **C2** `store.py` — `ScheduledJob` model + `JobStore` protocol with
  `InMemoryJobStore` and durable `SqliteJobStore` (survives restart).
- **C3** `runner.py` — `Scheduler.tick(now)` (deterministic, injected clock +
  host `JobRunner` callback) fires due jobs with a per-run **hard interrupt**
  (`asyncio.wait_for`), advances `next_run` with no backfill, records
  status/failures, and auto-disables after N consecutive failures; an
  unparseable schedule disables the job instead of crashing the tick.
  `run_forever` is the thin always-on driver.
- **C4** — `tools/builtin/automation.py` cron tools now back onto the
  `JobStore` (in-memory default; `configure_cron_store(SqliteJobStore(...))`
  points the tools and the `Scheduler` at one durable source of truth) and
  validate the schedule on create. Existing automation tool tests stay green.

Acceptance met (tests in `tests/scheduler/`): a registered job fires on
schedule and reschedules; a job registered before a simulated restart fires
from the reopened SQLite store; a runaway job is bounded by the hard interrupt.

Not done (deferred, lower value): `remote_trigger` / `push_notification` /
`subscribe_pr` remain intent envelopes (no delivery backend — that is the
gateway, gap #6); pre-run script injection; postgres job store. The host still
owns the `JobRunner` (e.g. wiring it to `agent.query(job.command)`) and the
process that calls `tick`/`run_forever`.

## Track #5 (DONE 2026-06-09) — Hook-Chain Self-Healing

The declarative hook-chain machinery already existed
(`contracts/hook_chains.py` + `runtime/hook_chains.py`'s `HookChainExecutor`)
but was dormant — host-driven `observe` with nothing in the loop feeding it.
Wired it in through the A1 lifecycle seam:

- Extended `RunLifecycleHook` with `on_error(context, *, output, events)` and
  `dispatch_error`; `SingleAgentRunner.run` dispatches it when a run terminates
  `FAILED`/`TIMED_OUT` (not user-cancelled), passing the run's event log. Hook
  exceptions are isolated so a recovery hook can't mask the original failure.
- Added `placeholders_for_event` to `runtime/hook_chains.py` and
  `HookChainLifecycleHook` (`runtime/single_agent/lifecycle/hook_chain_hook.py`):
  on failure it replays the run's events through a fresh per-run
  `HookChainExecutor` and hands each matched `FallbackSpec` to a host-supplied
  `spawn` callback (per-spawn error isolation). Spawning stays the host's job
  (it owns `run_subagent`), matching the executor's original design. Exported
  from `agent_driver.runtime`.

Acceptance (tests/runtime/test_hook_chain_lifecycle.py): a real run driven to
`FAILED` (max steps) dispatches `on_error`, the configured `run_failed` rule
fires, and the rendered fallback reaches the spawn callback; depth limits and
spawn-error isolation hold.

Cooldown/depth budgets are run-scoped (fresh executor per run). The existing
`tests/runtime/test_hook_chains.py` executor coverage is unchanged.

## Track #7 (DONE 2026-06-09) — Granular Permission Model

Added a composable, operator-authorable permission layer in
`agent_driver/permissions/`, layered on the existing per-call `tool_gate`
seam (so it complements, not replaces, the risk/side-effect guardrails and the
bash tool's read-only allowlist):

- **E1** `command_classifier.py` — `classify_command(cmd) -> CommandRisk`
  (`SAFE`/`CAUTION`/`DANGEROUS`/`CRITICAL` with reasons). Conservative,
  pattern-based detection of `rm -rf /`, pipe-to-shell, fork bombs, `mkfs`,
  `dd of=/dev/...`, `chmod -R 777 /`, `sudo`, force-push, `eval`, etc.
- **E2** `policy.py` — `PermissionMode` (`YOLO`/`STANDARD`/`STRICT`),
  `PermissionRule` (tool-name glob + command include/regex → allow/deny/ask),
  and `PermissionPolicy.decide(tool, args)`: explicit rules win first, then the
  mode default runs the classifier on command-bearing tools and maps the risk
  level to a decision.
- **E3** `gate.py` — `build_permission_gate(policy) -> ToolGate` maps decisions
  to `ToolGateAllow`/`Deny`/`Ask`; pass it as `tool_gate=` to
  `agent.run` / `session.send`.

Acceptance (tests/permissions/): the classifier levels are pinned; the policy
honors modes + explicit rules; and through the real runner a CRITICAL command
is denied (blocked trace) while a DANGEROUS command pauses the run with an
`approval_required` interrupt.

Not done (deferred): path-glob filesystem rules and an ML/learned classifier
(the heuristic covers the high-value destructive forms); an agent-level default
gate on `RunnerConfig` (today the gate is passed per run).

## Track #8 (DONE 2026-06-09) — Descriptor-First Multi-Provider

Replaced the ad-hoc `if provider == ...` construction chain (which lived in
the CLI and didn't even wire Anthropic) with a descriptor registry in
`agent_driver/llm/provider_descriptors.py` that separates the three concerns:

- **metadata** — `ProviderDescriptor` declares a provider's transport, default
  base URL / model, credential env vars, and what is required;
- **routing** — the caller picks a `ProviderSpec` (id + overrides); env fills
  gaps;
- **transport** — `resolve_provider` maps the descriptor's `ProviderTransport`
  to a concrete constructor in ONE place.

Built-in descriptors: `fake`, `openrouter`, `openai`, `vllm`, `ollama`,
`anthropic`. Out-of-tree providers call `register_provider_descriptor` (with
aliases). `cli/providers.py` now delegates to `resolve_provider` (wrapping
`ProviderResolutionError` as `CliProviderConfigError` to keep its contract), so
CLI/SDK/evals share one resolution path and `anthropic`/`openai` work in the
CLI for free. Exported from `agent_driver.llm`.

Acceptance (tests/llm/test_provider_descriptors.py + existing
tests/cli/test_providers.py): each transport resolves; env fill + spec
overrides; missing-required errors; alias + custom-descriptor registration; the
CLI provider contract is unchanged.

Not done (deferred): per-model capability descriptors (the existing
`provider_capabilities.py` heuristic still derives those at request time);
Bedrock/Vertex/Gemini transports (add a descriptor + a transport branch when a
real need lands).

## Track #4 (DONE 2026-06-09) — MCP Server (expose agent-driver itself)

The inverse of the built-in MCP *client*: `agent_driver/mcp_server/` lets an
external MCP client (Claude Code, Cursor, another agent) drive an agent-driver
`Agent` over the Model Context Protocol.

- **G1** `server.py` — `AgentMcpServer` wraps an `Agent` and exposes tools
  `agent_query`, `session_send`, `session_history`. `handle_request` is a
  transport-agnostic JSON-RPC dispatcher (`initialize` / `tools/list` /
  `tools/call` / `ping`, notifications return nothing, unknown methods →
  `-32601`). Tool failures are in-band MCP results (`isError`), not protocol
  errors. Dependency-free — does not require the optional `mcp` SDK.
- **G2** `stdio.py` — `serve_stream(server, lines, write)` is the testable
  newline-delimited JSON-RPC pump; `serve_stdio` binds it to real
  stdin/stdout.

Acceptance (tests/mcp_server/): `initialize`/`tools.list` shapes; `agent_query`
and a `session_send` → `session_history` round trip with JSON-safe structured
content; unknown-tool / missing-arg are in-band errors; unknown method is a
protocol error; `serve_stream` pumps a mixed batch (ok / blank / parse-error /
notification) correctly.

Not done (deferred): the `mcp` SDK adapter and an HTTP/SSE transport (the
JSON-RPC core is transport-ready); approval/interrupt and event-polling tools
(land with the gateway, #6).

## Track #6 (headless core DONE 2026-06-09) — Gateway

Scope this pass: the **headless core** only (transport-agnostic; no server
framework or platform SDK dependency). `agent_driver/gateway/`:

- **H1** `events.py` — `GatewayEvent` (`STARTED` / `ACTION_REQUIRED` /
  `COMPLETED` / `FAILED`, with `session_id` / `run_id` / `seq` / `data`) and
  `to_sse()` rendering an SSE frame (`id:`/`event:`/`data:`) for resumable
  streaming.
- **H2** `gateway.py` — `AgentGateway` over an `Agent`: `submit(session_id,
  text)` runs a turn and yields events, **parking** the run and emitting
  `ACTION_REQUIRED` when it pauses on an approval interrupt; `respond(session_id,
  run_id, action)` resumes the parked run and continues; `pending(session_id)`
  introspects parked runs. An optional `tool_gate` composes the #7 permission
  layer at submit.

This fills the real gap the SDK left open: a session-routed, approval-correlated
server surface a transport or platform adapter sits on (the existing
`adapters/sse.py` already covers pure token streaming).

Acceptance (tests/gateway/): `to_sse` frame shape; `submit` → COMPLETED; the
full `submit` → `ACTION_REQUIRED` (via a permission gate) → `pending` →
`respond(REJECT)` → terminal round trip; respond-without-pending raises.

Not done (deferred, by scope decision): a concrete ASGI/SSE HTTP server and
platform adapters (Telegram/Slack) — they bind this core to a transport/SDK and
were explicitly left out of this slice; live token streaming inside the
approval lifecycle; an OpenAI-compatible endpoint.

## Sequencing rationale

Track A is highest-leverage and lowest-risk: it is local to `llm/`,
deterministically testable, and improves every run's reliability. Track B
unlocks multi-session product scenarios on top of existing contracts.
Track C converts an already-present-but-inert tool surface into a working
feature. Gaps #4–#10 are recorded here as decision records and pulled into
a track only when a concrete scenario needs them.

## Docs rule

Keep this page current as tracks open and close. When a track closes,
collapse it to a one-line decision record and move detail to
`docs/archive/` per the unified-plan docs rule.
