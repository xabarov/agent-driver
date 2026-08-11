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
See [live-message controls](live-message-controls.md) for phase-aware steer,
redirect, NEXT, cancellation, Stop, events, and Postgres migration rules.

## Supported entry points by building block

| Import | What you get |
| --- | --- |
| `agent_driver.embedding` | **One aggregate namespace** re-exporting the embedding-essentials from the facades below (identity re-exports — no new API, nothing to drift): `create_agent`/`Agent`/`Session`, `RunnerConfig`/`runner_config_parameter_names`/`RunAbortHandle`, typed `RunContextBudget` + `resolve_run_context_budget`, rollback serialization, host-store protocols + durable impls (including `PostgresCommandQueueStore`), versioned live-message contracts/capability/NEXT-handoff helpers, `RunLifecycleHook`/`BaseRunLifecycleHook`/`RevisionRequest`, `ToolGate*`/`GateProvenance`, provider protocol + `FakeProvider`, `register_skill_tools`, `AgentRunInput`/`AgentRunOutput`/`AllowedPrompt`/`ResumeCommand`. Use this for a durable embedding from a single import root; use the per-concern facades below for the full surface. |
| `agent_driver.sdk` | `create_agent`, `query`, `Agent`, `Session`, `ToolSet`, `run_self_consistent`, `run_subagent`, `run_subagent_group` (concurrent fan-out joined under a `SubagentJoinPolicy` — WAIT_ALL / WAIT_ANY / K_OF_N / RACE / BEST_EFFORT) + `SubagentGroupResult`, `SubagentSpec`, `SubagentResult`, `AsyncSubagentManager`, `BackgroundSubagent`, `fork_subagent`, trace/support helpers, SDK error types, and — re-exported from `runtime` so the build/run path is a single import — `RunnerConfig` + `RunAbortHandle`. `create_agent` also takes `model_role_map` / `model_router` / `role_providers` (R-track routing) as build-path sugar. |
| `agent_driver.runtime` | `RunnerConfig`, `runner_config_parameter_names` (public filtering helper for direct and flattened settings), `CapabilitySettings` / `TrimmingSettings` / `CompactionSettings` (the context-management settings `RunnerConfig(trimming=…, compaction=…)` consumes), `resolve_run_context_budget`, `serialize_runtime_state_for_compatibility`, `keyword_relevance_primer`, runtime store factories, `RunAbortHandle`; **host-store protocols** `CheckpointStore` / `RuntimeEventLog` / `CheckpointRecord` / `StorageCapabilities`; **durable command/control stores** `CommandQueueStore` / `InMemoryCommandQueueStore` / `SqliteCommandQueueStore` / `PostgresCommandQueueStore`; live-message `live_message_capabilities` / `live_message_receipt` / `dispatch_next_turn`; **lifecycle-hook protocol** `RunLifecycleHook` / `BaseRunLifecycleHook` / `RevisionRequest`; **rubric hook + state read** `RubricLifecycleHook` / `RubricGradeInput` / `GraderVerdict` / `get_rubric_runtime_state` + `RubricRuntimeState`; **run/stream projections** `project_runtime_events` / `project_run_timeline` / `backfill_stream_events` / `summarize_run_lifecycle` / `tool_name_from_event` (canonical `TOOL_CALL_*` tool-name extractor) / `RunLifecycleSnapshot` / `RunLifecycleState` / `RunTimelineRow` / `RuntimeSessionDiagnostics` (all re-exported from the facade — no need to import `runtime.storage` / `.control` / `.lifecycle_hooks` / `.stream` / `.metadata_state`) |
| `agent_driver.contracts` | `AgentRunInput` / `AgentRunOutput`, `RunContextBudget` / `ContextBudgetDefaults` / `ResolvedRunContextBudget`, `HarnessProfile`, `ToolManifest`, enums, message/usage/event models, including `MemoryStep` / `MemoryStepKind` for host memory projections |
| `agent_driver.llm` | provider protocol + built-ins (`FakeProvider`, OpenAI-compatible, Ollama, Anthropic), `resolve_provider` / `ProviderSpec` / `ProviderDescriptor`, `HealthAwareRouter`, `ProviderRouteProfile` / `ProviderPreflightResult`, request-shape policy helpers, error classifier, `sanitize_request_messages` |
| `agent_driver.permissions` | `PermissionPolicy`, `PermissionRule` (incl. `path_under` scope predicate), `PermissionMode`, `build_permission_gate`, `classify_command` |
| `agent_driver.memory` | `build_memory_provider` (one-call opt-in), `MemoryProvider`, `StoreBackedMemoryProvider`, `FactExtractingMemoryProvider`, `EmbeddingMemoryProvider` + `MemoryEmbedder` (semantic recall), `InMemoryMemoryStore`, `SqliteMemoryStore` |
| `agent_driver.fs` | `FileBackend` protocol + `StateBackend` / `LocalFilesystemBackend` / `CompositeBackend`, `FileBackendError` |
| `agent_driver.execution` | `ExecutionBackend` protocol (route built-in `bash`/`read`/`write` through a host-injected backend) + `LocalExecutionBackend` / `FakeExecutionBackend` / `CompositeExecutionBackend`, `BackendCommandRunner` / `BackendFileIO` adapters, validated `Execution*Request`/`Execution*Result` / `ExecutionIdentity` / `ExecutionBounds` / `ArtifactRef` contracts, typed `ExecutionError` hierarchy, `EXECUTION_SCHEMA_VERSION`. **Capabilities & routing:** optional `CapabilityAwareBackend` (`capabilities()`), `ExecutionCapabilitySnapshot` / `CapabilityName` / `CapabilityStatus` / `ProgramInfo` / `ToolExecutionRequirement` / `RequirementCheck` / `EnvironmentBrief` contracts, and the `resolve_capability_snapshot` / `check_requirement` / `derive_environment_brief` / `render_environment_brief_text` / `capability_diagnostics` helpers. A tool declares `ToolManifest.execution_requirement`; an unmet hard requirement withholds it pre-model and denies it pre-dispatch. **Leases & workspace:** optional `LeaseCapableBackend` / `WorkspaceCapableBackend`, `ExecutionLeaseManager`, lease contracts (`ExecutionLeaseRequest` / `ExecutionLeaseRef` / `ExecutionLease` / `LeaseReceipt` / `WorkspacePaths` / ownership+state enums), workspace-op contracts (list/glob/grep/stat/delete), the `validate_workspace_path` path-safety validator, and the artifact bridge (`execution_artifact_to_context_ref` / `execution_artifact_reference_payload`). Configure a lease with `RunnerConfig.execution_lease_ownership` (or attach a host-owned lease via `app_metadata["execution_lease_ref"]`); one lease spans the run, all filesystem builtins route to it with backend-relative path safety, and it is released/detached on every exit (retained across pause; subagents isolate by default). **Jobs, events & control:** optional `JobCapableBackend` (`start_job`/`lookup_job`/`observe`/`snapshot`/`control`/`teardown`) for reconnectable long-running operations; job contracts (`ExecutionHandle` / `ExecutionEvent` / `ExecutionEventCursor` / `ExecutionEventPage` / `ExecutionTerminalSnapshot` / `ExecutionControlRequest`+`Receipt` / `TeardownReceipt` + `ExecutionJobState`/`ExecutionEventKind`/`ExecutionControlKind`/`ExecutionReasonCode`); and the `JobObserver` / `JobSession` / `stop_job` / `persist_job_recovery` helpers (duplicate-tolerant generation fencing, lost-start→indeterminate without re-dispatch, gap→snapshot, transport-loss resilience, and accepted-vs-applied-vs-teardown-confirmed as separate truthful facts). A handler's `report_tool_progress` (and a job's bounded observed events through it) surfaces as `TOOL_PROGRESS` runtime events correlated to the tool_call_id. **Compatibility kit:** `run_compliance(backend)` / `render_markdown` + the `ComplianceReport` / `ComplianceCheck` / `ComplianceStatus` / `ComplianceGroup` contracts — a deterministic, no-live-LLM/infra suite that qualifies any backend and reports passed/failed/unsupported/skipped/stale/no_claim per guarantee group (a group is `no_claim` unless advertised; an advertised-but-unproved guarantee is `failed`). See `docs/execution-backend-migration.md` and `examples/cookbook/21_backend_compliance.py`. |
| `agent_driver.harness` | `select_harness_profile`, `apply_system_slots`, `apply_tool_overrides`, `profile_excluded_tools` |
| `agent_driver.batch` | `BatchRunner`, `Trajectory`, `TrajectoryStore` backends, `compress_trajectory` / `compress_trajectories` |
| `agent_driver.evals` | `run_comparison` / `compare_aggregates` / `render_comparison`, `aggregate_trajectories`, `general_task_suite`, open-weight `presets`, replay helpers; **answer-quality judging** — deterministic `AnswerRubric` + `evaluate_answer_rubric` (checks a run's `answer` against must-contain/-not-contain/regex, free), the generic `LlmJudge` / `JudgeVerdict` / `AnswerJudge` (score an `(prompt, answer)` pair 0–1 via one aux LLM call), and `judge_trajectories` + `run_comparison(judge=…)` to fold a quality-median delta into an A/B |
| `agent_driver.scheduler` | `Scheduler`, `JobStore`, `ScheduledJob` |
| `agent_driver.gateway` | `AgentGateway` (headless session/approval core; bring your own transport) |
| `agent_driver.mcp_server` | `AgentMcpServer` (expose the agent over MCP) |
| `agent_driver.skills` | skill manifest/registry, curated packs |
| `agent_driver.agents` | Markdown-defined agent types (`AgentDefinition` + frontmatter), `parse_agent_markdown` / `load_agent_definitions`, an `AgentRegistry` (layered-precedence name→definition), and `agent_definition_to_spec` to bridge a definition to a `sdk.SubagentSpec` for `run_subagent` |
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
