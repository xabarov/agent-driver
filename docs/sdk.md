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
  governed execution wired. It also accepts `model_role_map` / `model_router` /
  `role_providers` (R-track routing) as build-path sugar — pass them instead of
  hand-building a `RunnerConfig`. `RunnerConfig` and `RunAbortHandle` are
  re-exported from `agent_driver.sdk`, so the whole build/run path is one import.
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

### JEV model-tier routing

The runtime can route one run to a configured semantic role before the first
LLM request. `JevTierRouter` asks a pinned decision model to choose `fast`,
`balanced`, or `strong`; `model_role_map` resolves those roles to the actual
generation models. The selected role is reused through the inner tool loop,
and provider errors or low confidence fall back to the heuristic router.

```python
import os

from agent_driver.llm import JevTierRouter, OpenRouterDecisionClient
from agent_driver.sdk import create_agent

decision_client = OpenRouterDecisionClient(
    api_key=os.environ["OPENROUTER_API_KEY"],
    model="typesafe/jev-1.13",
)
router = JevTierRouter(decision_provider=decision_client)

agent = create_agent(
    # primary_provider is the existing LlmProvider for normal generation.
    provider=primary_provider,
    model_router=router,
    model_role_map={
        "fast": "provider/cheap-fast-model",
        "balanced": "provider/mid-tier-model",
        "strong": "provider/frontier-model",
    },
)
```

The decision response is projected into raw-free `llm_route_decision` runtime
metadata with the selected role, model version, confidence, request ID, and
usage. Static tool and permission policy remains authoritative.

For an opt-in post-response gate, reuse the same decision client:

```python
from agent_driver.llm import JevQualityGate

quality_gate = JevQualityGate(
    decision_provider=decision_client,
    model="typesafe/jev-1.13",
    strong_role="strong",
)

agent = create_agent(
    provider=primary_provider,
    model_router=router,
    model_role_map={
        "fast": "provider/cheap-fast-model",
        "balanced": "provider/mid-tier-model",
        "strong": "provider/frontier-model",
    },
    quality_gate=quality_gate,
)
```

The gate runs only for a candidate from a non-strong role. A low-confidence or
insufficient answer gets at most one strong escalation; a recovery or
clarification request adds a fixed, policy-aware prompt for the next model turn.
JEV cannot change denied tools, approvals, retry limits, or irreversible-action
guards. Gate failures accept the existing deterministic result.

### JEV compaction pre-pass

When compaction is already eligible, `JevCompactionPrepass` can archive only
high-confidence, low-relevance exchanges before the existing compactor runs.
It is opt-in and fail-open; configure the provider object on `RunnerConfig` and
enable `enable_jev_compaction_prepass`:

```python
from agent_driver.llm import JevCompactionPrepass
from agent_driver.runtime import RunnerConfig

prepass = JevCompactionPrepass(
    decision_provider=decision_client,
    model="typesafe/jev-1.13",
)
config = RunnerConfig(
    enable_compaction=True,
    enable_jev_compaction_prepass=True,
    compaction_prepass=prepass,
)
agent = create_agent(provider=primary_provider, config=config)
```

Protected system/evidence/material-fact messages and the live turn always stay
in the request. `compaction_prepass` stores unit IDs, retention classes,
thresholds, hashes, usage, and reduction metrics without transcript text; JEV
failure or low confidence leaves the legacy compaction view unchanged.

Tool-arg truncation (a cheap pre-compaction pass) lives in `CompactionSettings`
(`enable_tool_arg_truncation`, `tool_arg_truncation_max_chars`).

### JEV memory durability gate

The fact-extraction provider can use the same decision client to screen writes
before they reach a durable memory store:

```python
from agent_driver.memory import MemoryDurabilityGate, build_memory_provider

memory = build_memory_provider(
    path="memory.sqlite",
    extractor=primary_provider,
    durability_gate=MemoryDurabilityGate(
        decision_provider=decision_client,
        model="typesafe/jev-1.13",
    ),
)
agent = create_agent(provider=primary_provider, memory_provider=memory)
```

The gate classifies each bounded candidate as durable, session-only, obsolete,
sensitive, or uncertain, then checks durable candidates for contradictions with
existing memory. Only accepted durable facts are written; secrets are redacted
before the decision request, and failures hold candidates instead of falling
back to raw durable turns. The run metadata contains a raw-free
`memory_durability` receipt and `memory_fact_provenance` for accepted facts.

### JEV rollout modes

Stage 5 keeps rollout separate from provider construction. Attach a policy to
`RunnerConfig` while the individual gates remain injected as above:

```python
from agent_driver.llm import JevRolloutSettings
from agent_driver.runtime import RunnerConfig

config = RunnerConfig(
    jev_rollout=JevRolloutSettings(
        mode="shadow",
        task_allowlist=("low_risk",),
        gate_modes={"memory": "active"},
        pinned_models={"routing": "typesafe/jev-1.13"},
        max_fallback_rate=0.20,
        max_latency_p95_ms=5000.0,
        min_observations_for_rollback=20,
    ),
)
agent = create_agent(provider=primary_provider, config=config)
```

`shadow` evaluates and records decisions without changing routing, tool-loop
transitions, or compaction input. `active` applies the bounded decision. A
controller can switch every gate off after the configured fallback, cost, or
p95 latency threshold is breached.

For the bounded Stage 6 task canary, use `JevCanarySettings` from
`agent_driver.evals.jev_stage6`. It keeps `normal_chat` allowlisted, promotes
routing and quality first, and holds compaction and memory in shadow while
calibrating live labels.

Stage 8 reviewed labels use `JevProductionLabelLedger` and
`JevOutcomeLabel`. The promotion evaluator requires independent labels across
multiple windows and never enables global default-on without an explicit
operator approval.

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
