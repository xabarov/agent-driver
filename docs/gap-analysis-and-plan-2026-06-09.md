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

## Track B (next) — Long-Term Cross-Session Memory (gap #2)

Goal: optional pluggable memory so multi-session agents can recall prior
context.

Steps (sketch):

- `MemoryProvider` ABC: `sync_turn`, `prefetch`, `shutdown`,
  optional `post_setup`; single active external provider at a time.
- Built-in backends: in-memory and sqlite; external providers via an
  out-of-tree plugin contract.
- Inject prefetched memory as a filter-safe context block (treated as
  source material, not active instructions), reusing the compaction
  preamble convention.

Acceptance: a two-session scenario recalls a fact stored in session one;
recall is bounded and does not break prompt caching mid-session.

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
