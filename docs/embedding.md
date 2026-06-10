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
| `agent_driver.sdk` | `create_agent`, `query`, `Agent`, `Session`, `ToolSet`, `run_subagent`, `SubagentSpec`, `SubagentResult`, `AsyncSubagentManager`, `BackgroundSubagent`, `fork_subagent`, SDK error types |
| `agent_driver.runtime` | `RunnerConfig`, `CapabilitySettings`, runtime store factories, `RunAbortHandle` |
| `agent_driver.contracts` | `AgentRunInput` / `AgentRunOutput`, `HarnessProfile`, `ToolManifest`, enums, message/usage/event models |
| `agent_driver.llm` | provider protocol + built-ins (`FakeProvider`, OpenAI-compatible, Ollama, Anthropic), `resolve_provider` / `ProviderSpec` / `ProviderDescriptor`, `HealthAwareRouter`, error classifier, `sanitize_request_messages` |
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

Runnable examples for most of these live in [`examples/cookbook/`](../examples/cookbook/README.md).

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
- **Extending vs embedding.** To *add* a capability (new provider, tool, store,
  hook), see [extending.md](extending.md). This page is for *consuming* the
  runtime from an app.
