# Extending agent-driver

How to add a new capability so it stays consistent with the rest of the
runtime. Written after the 2026-06 capability tracks (memory, scheduler,
permissions, MCP server, gateway, provider descriptors) which all follow the
same shape.

## The standard subsystem shape

A new capability is a small top-level package `agent_driver/<name>/` with up to
four layers, each optional depending on the capability:

1. **Contracts / models** — Pydantic `ContractModel` (or frozen dataclass) for
   the data the subsystem owns. Provider-neutral; no runtime imports.
   *Examples:* `memory.MemoryRecord`, `scheduler.ScheduledJob`,
   `permissions.PermissionPolicy`, `gateway.GatewayEvent`.
2. **A `Protocol` for the pluggable part** — so hosts can supply their own
   backend. *Examples:* `memory.MemoryStore`, `scheduler.JobStore`.
3. **Built-in backends** — usually an in-memory and a durable SQLite one.
   The SQLite backend subclasses the shared base (see below).
4. **A runtime adapter** — the thin piece that plugs the subsystem into the
   agent loop via an existing seam (below), kept out of the pure package when
   it must import runtime. *Example:* `runtime/single_agent/lifecycle/
   memory_hook.py` bridges `MemoryProvider` to the lifecycle seam so the
   `memory/` package stays runtime-free.

Keep the pure package free of `agent_driver.runtime` imports when it is
imported *by* the runtime (e.g. `memory` is imported by
`runtime.single_agent.types`); put the runtime-coupled adapter under
`runtime/` to avoid import cycles.

## Extension seams — which one to use

| You want to… | Use | Where |
| --- | --- | --- |
| React to run start / finalize / failure | `RunLifecycleHook` (`on_run_start`/`on_finalize`/`on_error`) | `runtime/lifecycle_hooks.py`; pass via `create_agent(lifecycle_hooks=...)` |
| Transform/observe each LLM call (inject prompt, filter tools, evict, observe) | `RunLifecycleHook.before_llm_request` (returns a replacement request) / `after_llm_response` | same hook + `dispatch_before_llm`/`dispatch_after_llm` in the LLM step |
| Inspect/transform a single tool call/result | `ToolHook` (`pre_tool_use`/`post_tool_use`) | `contracts/hooks.py`; dispatched in `tools/executor/governed.py` |
| Allow/deny/ask per planned call at runtime | `tool_gate` (`ToolGate` → Allow/Deny/Ask) | `runtime/tool_gate.py`; pass via `agent.run(tool_gate=...)` |
| Enforce a declarative permission policy | `permissions.build_permission_gate(policy)` (a `ToolGate`) | `permissions/` |
| Guard input/args/results in a pipeline | `GuardrailPipeline` | `tools/guardrails/` |
| Spawn a fallback when a run fails | `HookChainLifecycleHook` over `HookChainConfig` | `runtime/single_agent/lifecycle/hook_chain_hook.py` |
| Recall/persist long-term memory | `MemoryProvider` | `memory/`; wired as a lifecycle hook |
| Run scheduled work | `Scheduler` + `JobStore` | `scheduler/`; host owns the `JobRunner`; drive `tick` (deterministic) or `run_forever` (daemon — see `examples/cookbook/09_daemon.py`) |
| Run a prompt set with bounded concurrency | `BatchRunner(agent, concurrency=N)` | `batch/`; records trajectories to a `TrajectoryStore` |
| Shrink recorded trajectories for a training dataset | `compress_trajectories(items, max_tokens=N)` | `batch/compress.py`; keeps first/last turns, elides the middle |
| Add an LLM provider | `ProviderDescriptor` + `register_provider_descriptor` | `llm/provider_descriptors.py` |
| Shape the request per provider/model (prompt slots, tool exclusion, description overrides) | `HarnessProfile` | `contracts/profiles.py` + `harness/`; pass via `RunnerConfig(harness_profiles=...)` |
| Expose the agent to external clients | `AgentMcpServer` (MCP) / `AgentGateway` (sessions+approvals) | `mcp_server/`, `gateway/` |

Rule of thumb: **behavioral, run-scoped** capabilities are lifecycle hooks;
**per-call governance** is the tool gate / guardrails; **pluggable storage or
backends** are a `Protocol` + backends; **new providers** are descriptors.
Prefer composing an existing seam over editing the step loop
(`runtime/single_agent/lifecycle/steps.py`) — that loop is the generic driver.

## Shared primitives to reuse

- **`agent_driver.persistence.SqliteStoreBase`** — connection + WAL + lock +
  `_execute`/`_query`/`close`. Subclass it for a SQLite backend and declare
  your schema in `_init_schema`; do not re-implement the connection plumbing.
- **`agent_driver.registry.Registry`** — case-insensitive keyed registry with
  aliases, duplicate protection, identity-deduped `values()`. Use for an
  in-process registry (as `provider_descriptors` does) instead of a module
  `dict` + hand-rolled helpers.
- **`_MetadataView` typed state owners** (`runtime/metadata_state.py`) — new
  runtime `context.metadata` state goes through a typed owner, not raw string
  keys; register new keys in `docs/runtime-metadata.md` (a test enforces it).

## Operational thresholds & tuning

**Permission modes** (`permissions.PermissionMode`). Commands are scored by
`classify_command` into ordered risk levels `SAFE < CAUTION < DANGEROUS <
CRITICAL`; the mode decides what happens to a call that no explicit
`PermissionRule` matched:

| Mode | `SAFE` | `CAUTION` | `DANGEROUS` | `CRITICAL` |
| --- | --- | --- | --- | --- |
| `yolo` | allow | allow | allow | allow |
| `standard` (default) | allow | allow | **ask** | **deny** |
| `strict` | allow | **ask** | **deny** | **deny** |

`yolo` builds no gate at all. An `ask` outcome parks the run on an approval
interrupt (the chat loop's `/approve`/`/reject`, the gateway's `respond`).
Wire it via `agent.run(tool_gate=build_permission_gate(PermissionPolicy(mode=...)))`,
or the CLI `--permission-mode {yolo,standard,strict}` on `run`/`chat`.

**Batch concurrency** (`BatchRunner(agent, concurrency=4)`). An
`asyncio.Semaphore` caps in-flight runs; `concurrency` must be `>= 1`. Tune it
to the provider's rate limit, not the host's core count — runs are I/O-bound on
the model. Start at the default 4 and raise only until you see provider 429s.

**Scheduler bounds** (`Scheduler(...)`). Each fire is wrapped in a per-job hard
timeout (`default_timeout_seconds=300`). After `max_consecutive_failures=5` a
job auto-disables (status `disabled`) rather than retrying forever. `run_forever`
polls every `poll_interval_seconds=30` by default; set it below the shortest
interval schedule you register so jobs are not delayed by up to a full poll.

**Long-term memory** is opt-in: pass `memory_provider=` to `create_agent`, or
the CLI `--memory sqlite [--memory-path PATH]`. `post_setup()` runs once on
first turn; call `await agent.aclose()` (or `async with agent:`) to flush it.

**Harness profiles** (`RunnerConfig(harness_profiles=(HarnessProfile(...),))`)
shape the request per model without touching the step loop: `system_prefix` /
`system_suffix` wrap the assembled system prompt (applied before trimming so
they can't be trimmed away), `excluded_tools` ride the deny filter (the model
never sees them), and `tool_description_overrides` rewrite surfaced tool
descriptions. Selection is first-match over `match_models` (`fnmatch` globs;
empty = any model) against the request's resolved model — so per-model profiles
require the model to be pinned (e.g. via `set_model` / forced-model metadata);
empty-pattern profiles act as a provider-wide default.

**Anthropic prompt caching** is opt-in via `RunnerConfig(enable_prompt_cache=True)`
(CLI `--prompt-cache`; no-op for non-Anthropic providers). It places ephemeral
`cache_control` breakpoints at three assembly tiers — the tools catalog, the
system prompt, and the conversation prefix (the last message) — so each tier
unchanged next turn is billed at cache-read rates. Watch the payoff via the
cost ledger's `cache_hit_rate()` (see N1 cost governance). Anthropic ignores
breakpoints below its per-model minimum (≈1024 tokens), so short turns cache
nothing — expect gains on long system prompts / multi-turn chats.

## Quality bar (applies to every addition)

- A new package is dependency-light; heavy/optional deps go behind an extra.
- Pydantic models validate JSON-serializability of free-form `metadata`.
- Tests: pure logic unit-tested deterministically (inject clocks/ids), plus
  one integration test through the real runner where a runtime seam is used.
- `black` / `isort` clean; `pylint` 10/10 on new modules; keep the suite green.
