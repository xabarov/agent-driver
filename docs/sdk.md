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
- `run_self_consistent(...)` runs the same input multiple times and returns the
  plurality-vote consensus plus the vote distribution.
- `Agent.query(...)` and `Agent.run_text(...)` accept plain text, plus optional
  `reasoning_effort=` (thinking tier — `none/minimal/low/medium/high/xhigh/max`)
  and `model_role=` (role→model / difficulty-routing key). The same two kwargs are
  on `Session.send` / `Session.stream` / `Session.start`. Both default to `None`
  (inert); for full per-run control use `Agent.run(AgentRunInput(...))`.
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
| `default_max_steps` | config-level backstop when `AgentRunInput.max_steps` is unset | default `80`; use `None` only for intentionally unbounded loops |
| `default_max_tool_calls_per_step` | cap calls accepted from one model response before approval/execution | default `None`; set `1` for sequential evidence-led workflows; a run-level value overrides it |
| `budget_grace_enabled` | grants one bounded no-tools final-answer window after soft step/tool budgets | cost ceilings still hard-stop |
| `defer_primer` | surfaces relevant deferred tools before each LLM step | `keyword_relevance_primer()` is the generic default helper; `None` keeps pure `tool_search` behavior |

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
- Provider support artifacts can include `ProviderRouteProfile` /
  `ProviderPreflightResult` metadata so callers can inspect request-shape
  downgrades without making a live provider request.

## Capability Packs And Validation Gates

Product hosts can opt into redaction-safe capability-pack metadata for
continuous validation and release evidence. The built-in seed packs currently
cover `excel_workbook_chat` and `deep_research_chat_demo`; selecting one is
inert by default and only projects required evidence, scenario ids, gate status,
and skipped-gate reasons into trace summaries and support bundles.

```bash
agent-driver capability-pack dry-run \
  --pack-id deep_research_chat_demo \
  --scenario-id chat_demo.deep_research.source_report.v1 \
  --output-dir .agent-driver/capability-packs/deep-research

agent-driver capability-pack run-deterministic \
  --pack-id deep_research_chat_demo \
  --scenario-id chat_demo.deep_research.source_report.v1 \
  --output-dir .agent-driver/capability-packs/deep-research-run
```

`dry-run` never executes host commands. `run-deterministic` executes only
deterministic commands after conservative command guards, redacts command
output, writes `manifest.json`, `evidence_index.json`,
`validation_gates.json`, and per-command output artifacts, and marks
`support_bundle_artifact` passed when `--output-dir` persists the manifest.
Optional live/provider/UI/benchmark gates stay skipped with explicit reasons
until a host runs the corresponding policy-supervision gate.

Host manifests should use relative commands plus `AGENT_DRIVER_REPO` and
adapter-owned env vars such as `EXCEL_AI_BACKEND_DIR`; they should not embed
absolute local checkout paths or secret values.

See also:

- [SDK sessions](sdk-sessions.md)
- [SDK tools](sdk-tools.md)
- [SDK streaming](sdk-streaming.md)
- [SDK errors](sdk-errors.md)
