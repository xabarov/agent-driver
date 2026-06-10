# Cookbook

Short, self-contained examples for each agent-driver capability. Every script
runs **offline** (it uses `FakeProvider`, no API key) and exposes a `main()`
that `tests/examples/test_cookbook.py` executes, so the cookbook cannot rot.

Run any one directly:

```bash
python examples/cookbook/01_quickstart.py
```

| Script | Capability | Key APIs |
| --- | --- | --- |
| `01_quickstart.py` | Build an agent, run a query | `create_agent`, `agent.query` |
| `02_long_term_memory.py` | Recall a fact across runs/instances | `MemoryProvider`, `SqliteMemoryStore` |
| `03_permissions.py` | Deny a dangerous shell command | `classify_command`, `build_permission_gate` |
| `04_scheduler.py` | Fire a cron job (`tick`) | `Scheduler`, `JobStore`, `ScheduledJob` |
| `05_batch.py` | Generate trajectories for a prompt set | `BatchRunner`, `TrajectoryStore` |
| `06_gateway.py` | Session turn → approval → resume | `AgentGateway`, `tool_gate` |
| `07_mcp_server.py` | Expose the agent over MCP | `AgentMcpServer` |
| `08_providers.py` | Resolve / register a provider | `resolve_provider`, `ProviderDescriptor` |
| `09_daemon.py` | Run the scheduler `run_forever` loop firing agent turns | `Scheduler.run_forever`, `JobRunner` |
| `10_capabilities.py` | Wire prompt-cache + permission gate once | `CapabilitySettings`, `create_agent(tool_gate=...)` |
| `11_project_memory.py` | Load AGENTS.md + injection scan (E2/E3) | `load_project_memory`, context scanner |
| `12_subagent_routing.py` | Route subagents to per-role models (E6) | `RunnerConfig(subagent_model_routing=...)`, `run_subagent` |
| `13_eval_compare.py` | Baseline-vs-treatment, N-run median deltas (T0) | `run_comparison`, `render_comparison` |
| `14_filesystem_backends.py` | Route paths to scratch vs durable storage (E7) | `CompositeBackend`, `StateBackend`, `LocalFilesystemBackend` |
| `15_auxiliary_model.py` | Route compaction to a cheaper auxiliary model (E1) | `RunnerConfig(auxiliary_provider=..., auxiliary_model=...)` |
| `16_acp_adapter.py` | Serve an agent over the Agent Client Protocol (stdio) | `AgentAcpServer`, `serve_acp`, `request_permission` round-trip |
| `17_openai_server.py` | Serve an agent over an OpenAI-compatible HTTP API | `server.create_app`, `/v1/chat/completions` (stream + non-stream), `/v1/models` |

To use a real model, swap `FakeProvider(...)` for a descriptor-resolved
provider (see `08_providers.py`) — e.g. `resolve_provider(ProviderSpec(
provider_id="openrouter", model="..."))` with `OPENROUTER_API_KEY` set. See
[docs/extending.md](../../docs/extending.md) for how the pieces fit together.
