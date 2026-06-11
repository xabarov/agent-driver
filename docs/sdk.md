# SDK

The SDK is the product-facing surface over the runtime. Prefer it over direct
`SingleAgentRunner` wiring in applications.

```python
from agent_driver.llm import FakeProvider
from agent_driver.sdk import ToolSet, create_agent

agent = create_agent(
    provider=FakeProvider(response_text="ok"),
    tools=ToolSet.only(),
)
output = await agent.query("Summarize this task", run_id="run_1")
print(output.answer)
```

Core entrypoints:

- `create_agent(...)` builds an `Agent` facade with stores, tool registry and
  governed execution wired.
- `query(...)` is a one-shot helper for simple integrations.
- `Agent.query(...)` and `Agent.run_text(...)` accept plain text.
- `Agent.run(...)` accepts a full `AgentRunInput` for advanced control.
- `Agent.session(...)` returns a thread-scoped `Session`.
- `Agent.start(...)`, `Agent.stream_run(...)` and `Agent.stream(...)` expose
  background and streaming workflows.

## Capabilities (`RunnerConfig` / `CapabilitySettings`)

Opt-in capabilities are configured on `RunnerConfig`. The recently-added ones are
grouped in `CapabilitySettings` (`from agent_driver.runtime import
CapabilitySettings, RunnerConfig`); they can be passed as flat `RunnerConfig`
kwargs or as `RunnerConfig(capabilities=CapabilitySettings(...))` — both are
equivalent, and `config.<field>` reads work either way.

| Field | What it does | Notes |
| --- | --- | --- |
| `enable_prompt_cache` | Anthropic prompt-cache breakpoints (tools → system → conversation) | no-op for non-Anthropic providers |
| `auxiliary_provider` / `auxiliary_model` | route side tasks (compaction) to a cheaper model | falls back to the main provider; spend separated by model in the cost ledger |
| `project_memory_sources` | layer AGENTS.md/CLAUDE.md files into the system prompt | injection-scanned at ingestion; caps via `project_memory_max_file_chars` / `project_memory_max_total_chars` |
| `harness_profiles` | per-model prompt slots / tool exclusion / description overrides | first-match over `match_models` globs (case-insensitive) |
| `tool_concurrency_limit` | cap parallel tool execution | else `AGENT_DRIVER_TOOL_CONCURRENCY` / default 8 |
| `subagent_model_routing` | `{agent_type: model}` for child runs | explicit `forced_model` overrides; routed model rides `forced_model` |

Tool-arg truncation (a cheap pre-compaction pass) lives in `CompactionSettings`
(`enable_tool_arg_truncation`, `tool_arg_truncation_max_chars`).

Permission gating is wired once at construction:
`create_agent(..., tool_gate=build_permission_gate(PermissionPolicy(mode=...)))`.
The gate applies to every run/stream/session turn; a per-call `tool_gate=`
overrides it. See `examples/cookbook/10_capabilities.py`.

Output diagnostics:

- `output.context.pressure` is the stable context-pressure state.
- `output.context.recommendation` gives the caller a compact next-action hint.
- `agent.summarize(output)` or `summarize_output(output)` returns
  `TraceSummary`.
- `agent.support_bundle(output)` returns a redacted support-bundle recipe.

See also:

- [SDK sessions](sdk-sessions.md)
- [SDK tools](sdk-tools.md)
- [SDK streaming](sdk-streaming.md)
- [SDK errors](sdk-errors.md)
