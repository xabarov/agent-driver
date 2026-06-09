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

## Track C (then) — Real Scheduler For Existing Intents (gap #1)

Goal: turn the existing `cron_*` / `remote_trigger` / `push_notification`
intent records into a durable executor (the tool surface already exists).

Steps (sketch):

- durable job store (reuse sqlite/postgres backends) for cron jobs and PR
  subscriptions;
- a scheduler loop: 5-field cron + human intervals, catchup window, a hard
  per-run interrupt to prevent runaway loops, resume after restart;
- optional pre-run script injection feeding stdout into the prompt.

Acceptance: a registered cron job fires on schedule, is durable across a
restart, and is bounded by the hard interrupt.

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
