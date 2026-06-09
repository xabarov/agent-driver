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
| Run scheduled work | `Scheduler` + `JobStore` | `scheduler/`; host owns the `JobRunner` + tick loop |
| Add an LLM provider | `ProviderDescriptor` + `register_provider_descriptor` | `llm/provider_descriptors.py` |
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

## Quality bar (applies to every addition)

- A new package is dependency-light; heavy/optional deps go behind an extra.
- Pydantic models validate JSON-serializability of free-form `metadata`.
- Tests: pure logic unit-tested deterministically (inject clocks/ids), plus
  one integration test through the real runner where a runtime seam is used.
- `black` / `isort` clean; `pylint` 10/10 on new modules; keep the suite green.
