# Embedding agent-driver (public API surface)

`agent-driver` is a library: you import it into your application. This page is
the **supported public surface** — the modules and names an embedder can rely on
— plus the stability policy. Anything not listed here (notably
`agent_driver.runtime.single_agent.*` and any name with a leading underscore) is
internal and may change without notice.

The package ships a `py.typed` marker (PEP 561), so type checkers in your project
pick up agent-driver's type hints.

## Start here

```python
from agent_driver.sdk import create_agent, ToolSet
from agent_driver.llm import FakeProvider  # swap for a real provider

agent = create_agent(provider=FakeProvider(response_text="ok"), tools=ToolSet.only())
output = await agent.query("Summarize this task", run_id="r1")
print(output.answer)
```

See [SDK](sdk.md) for the full `Agent` surface and [capabilities](sdk.md#capabilities-runnerconfig--capabilitysettings).

## Supported entry points by building block

| Import | What you get |
| --- | --- |
| `agent_driver.embedding` | **One aggregate namespace** re-exporting the embedding-essentials from the facades below (identity re-exports — no new API, nothing to drift): `create_agent`/`Agent`/`Session`, `RunnerConfig`/`runner_config_parameter_names`/`RunAbortHandle`, host-store protocols + durable impls (`SqliteRuntimeStore`, `SqliteApprovalConsumptionStore`, `SqliteAbortLifecycleStore`, …), `RunLifecycleHook`/`BaseRunLifecycleHook`/`RevisionRequest`, `ToolGate*`/`GateProvenance`, provider protocol + `FakeProvider`, `register_skill_tools`, `AgentRunInput`/`AgentRunOutput`/`AllowedPrompt`/`ResumeCommand`. Use this for a durable embedding from a single import root; use the per-concern facades below for the full surface. |
| `agent_driver.sdk` | `create_agent`, `query`, `Agent`, `Session`, `ToolSet`, `run_self_consistent`, `run_subagent`, `SubagentSpec`, `SubagentResult`, `AsyncSubagentManager`, `BackgroundSubagent`, `fork_subagent`, trace/support helpers, SDK error types |
| `agent_driver.runtime` | `RunnerConfig`, `runner_config_parameter_names` (public filtering helper for direct and flattened settings), `CapabilitySettings`, `keyword_relevance_primer`, runtime store factories, `RunAbortHandle`; **host-store protocols** `CheckpointStore` / `RuntimeEventLog` / `CheckpointRecord` / `StorageCapabilities`; **durable command/control store** `CommandQueueStore` / `InMemoryCommandQueueStore` / `SqliteCommandQueueStore`; **lifecycle-hook protocol** `RunLifecycleHook` / `BaseRunLifecycleHook` / `RevisionRequest`; **run/stream projections** `project_runtime_events` / `project_run_timeline` / `backfill_stream_events` / `summarize_run_lifecycle` / `RunLifecycleSnapshot` / `RunLifecycleState` / `RunTimelineRow` / `RuntimeSessionDiagnostics` (all re-exported from the facade — no need to import `runtime.storage` / `.control` / `.lifecycle_hooks` / `.stream`) |
| `agent_driver.contracts` | `AgentRunInput` / `AgentRunOutput`, `HarnessProfile`, `ToolManifest`, enums, message/usage/event models |
| `agent_driver.llm` | provider protocol + built-ins (`FakeProvider`, OpenAI-compatible, Ollama, Anthropic), `resolve_provider` / `ProviderSpec` / `ProviderDescriptor`, `HealthAwareRouter`, `ProviderRouteProfile` / `ProviderPreflightResult`, request-shape policy helpers, error classifier, `sanitize_request_messages` |
| `agent_driver.permissions` | `PermissionPolicy`, `PermissionRule` (incl. `path_under` scope predicate), `PermissionMode`, `build_permission_gate`, `classify_command` |
| `agent_driver.memory` | `MemoryProvider`, `StoreBackedMemoryProvider`, `InMemoryMemoryStore`, `SqliteMemoryStore` |
| `agent_driver.fs` | `FileBackend` protocol + `StateBackend` / `LocalFilesystemBackend` / `CompositeBackend`, `FileBackendError` |
| `agent_driver.harness` | `select_harness_profile`, `apply_system_slots`, `apply_tool_overrides`, `profile_excluded_tools` |
| `agent_driver.batch` | `BatchRunner`, `Trajectory`, `TrajectoryStore` backends, `compress_trajectory` / `compress_trajectories` |
| `agent_driver.evals` | `run_comparison` / `compare_aggregates` / `render_comparison`, `aggregate_trajectories`, `general_task_suite`, open-weight `presets`, replay helpers |
| `agent_driver.scheduler` | `Scheduler`, `JobStore`, `ScheduledJob` |
| `agent_driver.gateway` | `AgentGateway` (headless session/approval core; bring your own transport) |
| `agent_driver.mcp_server` | `AgentMcpServer` (expose the agent over MCP) |
| `agent_driver.skills` | skill manifest/registry, curated packs |
| `agent_driver.observability.cost_ledger` | `CostLedger`, `Pricing`, `register_pricing`, `estimate_cost_usd` |
| `agent_driver.security` | `scan_context_text` (ingestion injection scanner) |

The `agent_driver.tools` facade also exposes `register_skill_tools` for hosts
that deliberately assemble a narrow registry instead of registering the full
built-in tool pack.

Runnable examples for most of these live in [`examples/cookbook/`](../examples/cookbook/README.md).
For a full durable-embedding assembly — host stores + a custom governed tool + a lifecycle hook + an
approval gate + pause/approve/resume + a durable abort, using only these supported facades — see
[`examples/cookbook/19_embedded_e2e.py`](../examples/cookbook/19_embedded_e2e.py).

## Stability policy

- **Pre-1.0 (`0.x`).** The entry points above are the intended public surface,
  but minor versions may still break them; pin a version and read the changelog.
- **Internal = not supported.** `agent_driver.runtime.single_agent.*`,
  `*.lifecycle.*`, and any `_underscore` name are implementation detail — don't
  import them in application code (use the SDK / building-block entry points).
- **Contracts are the wire boundary.** `AgentRunInput` / `AgentRunOutput` and the
  other `contracts` models are validated and round-trippable; treat their fields
  as the stable data contract. A schema-snapshot test guards public contract
  fields against accidental change.
- **Export snapshot + deprecation policy.** The exact `__all__` of the embedding
  facades (`agent_driver.sdk`, `.runtime`, `.tools`) is pinned by an
  export-snapshot test — adding a public name is a deliberate surface change
  (golden set + changelog), and *removing* one is a breaking change: a removed
  name is first announced in the changelog and kept working for at least one
  minor release before the symbol is dropped. New additions are additive and
  need no deprecation window.
- **Extending vs embedding.** To *add* a capability (new provider, tool, store,
  hook), see [extending.md](extending.md). This page is for *consuming* the
  runtime from an app.
