# Agent Driver SDK Quality Deep Analysis

Status: active reference for Unified Work Plan Phases 3 and 7. Do not freeze
new public SDK contracts before Phase 1 runtime state/contract work.

Дата: 2026-05-31.

Цель: оценить качество публичного SDK слоя `agent_driver` после закрытых
планов в `docs`, сравнить его с Anthropic/Claude Agent SDK, OpenClaude и
Hermes Agent, и зафиксировать что осталось сделать, чтобы SDK ощущался как
стабильный продуктовый runtime, а не только как удобная обертка над внутренним
loop.

## Executive Summary

`agent_driver` уже прошел важный рубеж: это не прототип и не один runner-file.
Есть app-facing facade, durable contracts, stream projection, governed tools,
custom tool registration, tool packs, storage backends, interrupt/resume,
steering controls, subagents, research evidence, observability и chat-demo как
интеграционный gate.

Главная проблема SDK качества теперь не в нехватке возможностей. Наоборот,
возможностей много. Проблема в том, что публичная поверхность еще не полностью
отделена от внутренней формы runtime:

- пользователь SDK все еще должен знать `AgentRunInput`, `RunnerConfig`,
  `ToolPolicyInput`, `graph_preset`, `app_metadata`, отдельные stores и иногда
  внутренние metadata conventions;
- `Agent.stream()` реализован через polling persisted event log, а не через
  первый-класс live stream primitive;
- session model есть технически через `thread_id`, stores и event logs, но нет
  удобного SDK object model уровня `Session`;
- tool API уже сильный, но custom tool decorator требует больше boilerplate,
  чем у Anthropic/OpenClaude, и пока не дает in-process MCP-style server
  abstraction;
- SDK facade тонкий и качественный, но runtime state, provider adapters,
  tool executor и trace summary все еще держат большие internal modules,
  поэтому изменение SDK behavior легко цепляет внутренние файлы.

Короткий вердикт: **SDK уже пригоден для embedded backend usage и demo apps,
но еще не готов называться стабильным standalone agent SDK без оговорок.**
Следующий слой работы должен быть не добавлением новой агентной магии, а
упаковкой существующей силы в более явные SDK primitives: `AgentClient`,
`Session`, `Run`, `Stream`, `Tool`, `StoreBundle`, `ContextPolicy`,
`TraceSummary`.

## Current SDK Surface

### What Exists

Публичная точка входа:

- `agent_driver.sdk.create_agent(...)`;
- `Agent.run(...)`;
- `Agent.run_text(...)`;
- `Agent.stream(...)`;
- `Agent.resume(...)` shortcuts: `approve`, `reject`, `edit`, `cancel`,
  `clarify`;
- steering helpers: `control`, `enqueue`, `set_model`, `set_permission_mode`,
  `cancel_queued_message`;
- subagent helpers: `run_subagent`, `fork_subagent`, `SubagentSpec`;
- bootstrap helpers: `build_default_registry`, `sdk_config_from_env`.

Смежная публичная поверхность:

- `ToolSet.only`, `ToolSet.packs`, `ToolSet.from_preset`, risk/side-effect
  filters;
- `@custom_tool`, `register_custom_tool`, `register_custom_function`;
- `agent_driver.adapters.sse_event_stream` and CLI replay/tail adapters;
- contracts in `agent_driver.contracts`;
- storage factory and runtime store backends;
- observability helpers and trace summary.

### Strong Points

1. **Composable runtime wiring is real.** `create_agent` deep-copies config,
   builds a filtered registry, validates unknown tool names, wraps the governed
   executor and returns a small facade. This is the right SDK instinct:
   ergonomic default, advanced escape hatch.

2. **ToolSet is one of the best public API pieces.** It gives explicit
   selection by names, packs, presets, risk, side effects, profiles and
   application tags. That maps well to production embedding where app owners
   want "safe", "dev", "all", or custom pack composition.

3. **Human-in-the-loop is not an afterthought.** `Agent.run(..., tool_gate=...)`
   and resume shortcuts expose approval/edit/reject/clarify flows at the SDK
   level, not only in chat-demo.

4. **Streaming events are transport-neutral.** `RunStreamEvent` plus SSE/CLI
   adapters is a good separation: SDK emits normalized runtime stream events,
   host apps choose transport.

5. **Custom tools exist in Python-native form.** `@custom_tool` and
   `tool_from_function` infer an argument schema from the Python signature,
   attach manifest metadata and wrap the handler.

6. **Closed research/subagent/Python plans improved product behavior.** The
   latest docs show that research evidence gates, dynamic prompt fragments,
   source evidence, subagent synthesis, steering, Python execution and
   trace-summary verdicts are mostly implemented and live-probed.

### Weak Points

1. **The SDK facade is still mostly a pass-through to `AgentRunInput`.**
   `run_text` is easy, but real app usage quickly falls back to manually
   constructing `AgentRunInput(agent_id, graph_preset, stream, app_metadata,
   tool_policy, response_format, tool_choice, ...)`.

2. **No first-class `Session` object.** There is `thread_id`, `run_id`,
   checkpoint/event storage and session stores, but SDK users do not get:
   `session = agent.create_session(...)`, `session.send(...)`,
   `session.resume(...)`, `session.history()`, `session.fork()`.

3. **`Agent.stream()` is polling-based.** It starts `self.run(...)` in a task
   and polls `event_log.list_for_run(...)` every 20ms by default. This is good
   enough and durable, but for SDK quality it should be presented as an
   implementation detail behind a live stream object with cancellation,
   backpressure semantics, final output access and reconnect cursor.

4. **Runtime metadata is too visible indirectly.** Many product behaviors are
   stored in `context.metadata` keys. The refactoring plan already calls this
   out. From SDK perspective, this matters because app developers will
   eventually depend on output metadata fields that were never designed as
   stable contracts.

5. **Provider adapters are too large and uneven.** OpenAI-compatible and
   Anthropic providers normalize request/response/tool/stream/usage behavior
   in big files. SDK consumers need stable provider error classes, request IDs,
   retry/timeout configuration and usage accounting; currently those are
   partly present but not yet a clean SDK-level story.

6. **Public docs start with low-level runtime.** `README.md` quick start uses
   `SingleAgentRunner` and runtime store wiring instead of leading with
   `create_agent`. The docs examples are useful, but the top-level developer
   path still feels like internal runtime documentation.

## Comparison: Anthropic Python SDK

Anthropic's lower-level Python SDK is intentionally small: `Anthropic` /
`AsyncAnthropic`, `client.messages.create(...)`, `client.messages.stream(...)`,
typed errors, retries, timeouts, request IDs, token counting and tool helpers.

Relevant current practices from official docs:

- streaming can be used either as raw event iteration with
  `messages.create(..., stream=True)` or through `messages.stream(...)` helper
  that accumulates text and exposes final message;
- token counting is first-class through `messages.count_tokens(...)`;
- tools can be defined as Python functions with `@beta_tool`, deriving schema
  from signature/docstring;
- errors are typed (`APIConnectionError`, `RateLimitError`,
  `APIStatusError`, etc.), responses expose public `_request_id`, retries and
  timeouts are configurable globally or per request.

Sources:

- https://platform.claude.com/docs/en/api/sdks/python
- https://github.com/anthropics/anthropic-sdk-python

What this means for `agent_driver`:

- Add an SDK-level `stream()` helper that can either yield raw events or expose
  accumulated text/final output, mirroring the "raw stream vs helper stream"
  split.
- Add provider-neutral typed SDK errors and preserve provider request IDs in
  output/trace metadata.
- Add a token counting/context estimate helper at SDK level, not only in
  runtime trimming internals.
- Simplify custom tool creation: docstring/signature should be enough for the
  common case, while risk/remediation metadata remains available for governed
  mode.

## Comparison: Claude Agent SDK

Claude Agent SDK is the closest external shape to `agent_driver`, because it is
not just a Messages client. It has `query(...)`, `ClaudeSDKClient`, sessions,
custom tools, MCP servers, permission modes, hooks, subagents and partial
streaming.

Relevant current practices from official docs:

- the simple path is `query(prompt=..., options=ClaudeAgentOptions(...))`;
- streaming input mode with client object is the recommended multi-turn mode;
- sessions persist automatically and can be continued, resumed or forked;
- session utilities include listing sessions, reading messages, renaming,
  tagging and metadata lookup;
- custom tools are declared with `@tool`, can be wrapped into an in-process MCP
  server, and can be allowed by names such as `mcp__server__tool`;
- permission modes are explicit: default, accept edits, plan, don't ask,
  bypass permissions;
- hooks are first-class lifecycle extension points (`PreToolUse`,
  `PostToolUse`, `Stop`, `SessionStart`, etc.), with matcher filters and
  permission decisions;
- partial streaming is opt-in and yields raw stream events alongside normal
  assistant/result messages.

Sources:

- https://code.claude.com/docs/en/agent-sdk/overview
- https://code.claude.com/docs/en/agent-sdk/python
- https://code.claude.com/docs/en/agent-sdk/sessions
- https://code.claude.com/docs/en/agent-sdk/streaming-output
- https://code.claude.com/docs/en/agent-sdk/hooks

What this means for `agent_driver`:

- Add a high-level `query(...)` or `agent.query(...)` path for one-shot tasks
  where users do not construct `AgentRunInput`.
- Add a first-class `Session` facade for multi-turn and persisted workflows.
- Treat `permission_mode` as a typed SDK enum/config, not a string in
  `app_metadata` or queued control payload.
- Promote hooks from "runtime/tool advanced feature" into a documented SDK
  extension surface with names, ordering, timeout behavior and returned
  decisions.
- Add session utilities: list/read/fork/tag/delete or equivalents over the
  durable event/checkpoint stores.

## Comparison: OpenClaude

Local repo reviewed:

- `/home/roman/pyprojects/ML/openclaude`

OpenClaude has a more mature SDK/product split in some areas:

- SDK entrypoint is bundled separately and explicitly checks for CLI/TUI stub
  leaks at module load time. That is an excellent product-quality guard: SDK
  must not accidentally import React/Ink/TUI code.
- It exposes `query`, `queryAsync`, session functions, unstable v2 session
  lifecycle, `tool(...)`, `createSdkMcpServer(...)`, typed SDK errors and
  generated public types from a dedicated SDK barrel.
- It has tests for SDK query lifecycle, concurrency, preserved segments,
  casing, generated types, session functions, permissions, MCP cleanup, MCP
  SDK tools and package consumer types.
- It tracks token budget and continuation behavior explicitly, and it has
  auto-compact cooldown/circuit-breaker tests.

Useful patterns to port:

- **SDK isolation test.** Add an agent-driver package test that imports
  `agent_driver.sdk` in a minimal environment and asserts it does not import
  CLI/chat-demo/frontend-only modules.
- **Package consumer tests.** Add tests that install/import from the public
  package surface exactly as a downstream app would.
- **Session lifecycle test matrix.** Create, run, resume, fork, list, read,
  tag/rename/delete if supported.
- **Context/cooldown state as SDK-visible diagnostics.** OpenClaude's explicit
  budget/cooldown tracking maps directly to our early context-pressure plan.

## Comparison: Hermes Agent

Local repo reviewed:

- `/home/roman/pyprojects/ML/hermes-agent`

Hermes is more of an agent platform/CLI/gateway ecosystem than a minimal SDK,
but several design choices matter for `agent_driver`:

- It treats skills/plugins/tools as product-level extensions, not just
  function schemas.
- Skill loading resolves supporting files, config vars, setup notes and
  platform-disabled state before injecting instructions.
- Manual partial compression is boundary-aware: compress head, preserve a
  recent tail verbatim, snap to user-turn boundaries, protect role alternation.
- Kanban triage/specification converts rough ideas into structured task specs:
  goal, approach, acceptance criteria, out of scope.
- External docs and local code emphasize multi-provider routing, OpenAI-
  compatible API/proxy surfaces, credential pools, gateway/messaging channels,
  MCP catalog/configuration and persistent memory.

Sources:

- https://hermes-agent.ai/tools
- https://github.com/mudrii/hermes-agent-docs

Useful patterns to port:

- **Boundary-aware context compression controls.** `agent_driver` already has
  compaction internals; SDK should expose a safe "summarize up to here" /
  "preserve last N exchanges" operation.
- **Task spec helper.** For long-running SDK workflows, a small
  `TaskSpec(goal, approach, acceptance_criteria, out_of_scope)` contract would
  be more useful than only free-form prompt strings.
- **Extension catalog projection.** Tool/skill/MCP catalogs should have
  separate projections for model prompt, UI, docs and SDK discovery.
- **Gateway orientation.** `agent_driver` can remain small, but SDK should make
  FastAPI/SSE, OpenAI-compatible proxy and CLI embedding first-class recipes,
  not scattered examples.

## Current Plans: What Is Closed

From `docs/openclaude-improvement-plan-2026-05-29.md` and
`docs/research-quality-improvement-plan-2026-05-31.md`, the following SDK-
adjacent behaviors are effectively closed:

- dynamic prompt assembly by effective tool surface;
- research depth classification and source-verified report guard;
- `web_search -> web_fetch -> synthesis` evidence loop;
- untrusted web output boundary;
- source evidence and citation shelf metadata;
- trace-summary research/subagent/Python verdicts;
- steering controls and semantic routes;
- subagent delegation/synthesis UX and trace criteria;
- Python execution availability/guarding/recovery in chat-demo;
- compaction notification UX plan;
- reusable chat-demo logic moved into `agent_driver` for transcript,
  observability, Phoenix helpers, SSE helpers and runtime chat policy.

This is important: the next SDK pass should not reopen those product questions
from scratch. It should package the already-working behaviors into stable
contracts and higher-level entrypoints.

## What Remains To Do

### P0: Stabilize The Public SDK Shape

- Add `agent.query(...)` / top-level `query(...)` as the simplest one-shot API.
  It should accept `text`, optional `tools`, `model_role`, `session`, `stream`,
  `response_format`, `tool_choice`, `deadline_seconds`.
- Add `Agent.new_session(...)` returning a `Session` object:
  `send`, `stream`, `resume`, `fork`, `history`, `runs`, `close`.
- Add a `Run` or `RunHandle` object for long-running runs:
  `run_id`, `abort()`, `events()`, `final()`, `checkpoint()`.
- Hide `graph_preset` from common SDK usage. Keep it advanced/internal unless
  multiple graph presets become real public products.
- Replace stringly permission modes with a public enum/config object.
- Make `SdkConfig` more complete: provider config, store config, default model,
  timeout/retry, default tools, permission mode, workspace cwd.

### P0: Contract Guardrails

- Implement Phase 12 from the refactoring plan:
  contract snapshot tests for `AgentRunInput`, `AgentRunOutput`,
  `RuntimeEvent`, `ToolManifest`, `ToolTrace`, interrupt/resume payloads.
- Add tests that output metadata remains stable or explicitly versioned.
- Add tests that typed runtime snapshots do not leak into public output without
  a deliberate mapping.
- Add package consumer tests importing only `agent_driver.sdk`,
  `agent_driver.tools`, `agent_driver.contracts`, `agent_driver.adapters`.

### P0: Metadata To Typed Runtime State

- Execute Phase 1-2 of `agent-driver-refactoring-plan-2026-05-31.md`.
- Produce `docs/runtime-metadata.md` inventory:
  owner, producer, consumer, persistence need, UI relevance.
- Introduce typed state helpers for:
  `LoopControlState`, `ToolLoopState`, `PlanningRuntimeState`,
  `ResearchRuntimeState`, `StreamingRuntimeState`, `CompactionRuntimeState`.
- Forbid new ad hoc `context.metadata[...]` keys in code review except through
  owned helpers.

### P1: Streaming SDK Upgrade

- Replace raw polling details with a stream helper object:
  `async with agent.stream(...) as stream`.
- Provide:
  `stream.events()`, `stream.text_deltas()`, `stream.final_output()`,
  `stream.cancel()`, `stream.cursor`.
- Keep event-log polling as durable implementation, but support direct live
  event queue later.
- Expose reconnect/backfill as a documented SDK path, not only SSE adapter
  mechanics.

### P1: Tool SDK Upgrade

- Make `@custom_tool` less demanding for the common case:
  use docstring/signature defaults, warn or require remediation only for
  governed/high-risk profiles.
- Add a `tool(...)` helper returning a portable tool definition, parallel to
  Claude Agent SDK and OpenClaude.
- Add in-process MCP server helper or "tool server" abstraction:
  `create_sdk_tool_server(name, tools=[...])`.
- Add tool catalog projections:
  `for_model_prompt`, `for_ui`, `for_docs`, `for_runtime_execution`.
- Add manifest linting docs/tests: descriptions, risk, side effect,
  idempotency, output budget, remediation hints.

### P1: Provider SDK Quality

- Decompose OpenAI-compatible and Anthropic providers as planned.
- Add provider conformance tests for:
  tool calls, text-form fallback, usage, streamed deltas, provider errors,
  request IDs, retries/timeouts.
- Expose typed provider errors at SDK level.
- Surface request IDs and provider rejection payload summaries in output trace.
- Add explicit `max_retries` and `timeout` SDK config similar to Anthropic.

### P1: Context Pressure And Early Compaction

- Execute Phase 10 from the refactoring plan:
  `context_usage_ratio`, states `ok`, `early_warning`,
  `delegate_or_summarize`, `compact_recommended`, `blocking`.
- Add nudges around 35-45 percent context usage. The 92 percent threshold
  should remain emergency behavior, not the first moment the agent learns it is
  close to trouble.
- Add SDK-visible diagnostics:
  `output.context.pressure`, `output.context.recommendation`,
  `stream event: context_pressure_changed`.
- Add manual partial compression controls inspired by Hermes:
  `session.compact(up_to="here", keep_last=2)`.

### P1: Observability Productization

- Split `run_trace_summary.py` into analyzers as planned.
- Provide SDK helper:
  `agent.trace(run_id).summary()` or `summarize_run(output)`.
- Keep support bundle/redaction as public SDK recipes.
- Add a stable `TraceSummary` contract rather than an untyped dict.

### P2: Documentation And Developer Experience

- Rewrite README quick start around `create_agent` / `agent.query`.
- Move low-level `SingleAgentRunner` bootstrap to "Advanced runtime wiring".
- Add docs pages:
  `docs/sdk.md`, `docs/sdk-sessions.md`, `docs/sdk-tools.md`,
  `docs/sdk-streaming.md`, `docs/sdk-errors.md`.
- Add migration note: public API still early, but these names are intended as
  stable.
- Add "module ownership map" to docs as planned.

### P2: OpenAI-Compatible / Managed-Agent Gateway

This is not mandatory for core SDK, but it is strategically useful:

- expose agent-driver as an OpenAI-compatible endpoint for simple clients;
- expose SSE event stream and support tool progress;
- document where this differs from Anthropic Managed Agents/OpenClaw-style
  Agent/Environment/Session/Event APIs.

## Suggested Implementation Order

1. Metadata inventory and contract snapshots.
2. Public SDK shape: `query`, `Session`, `RunHandle`, richer `SdkConfig`.
3. Streaming helper object and reconnect/backfill docs.
4. Tool SDK polish: simpler decorator, `tool(...)`, catalog projections.
5. Provider conformance and typed SDK errors.
6. Early context pressure state and manual partial compaction.
7. Observability analyzers and stable `TraceSummary`.
8. README/docs rewrite and package consumer tests.

This order keeps the system boring in the right way: first make state and
contracts explicit, then improve ergonomics, then change behavior around early
context pressure.

## Quality Bar For "SDK Is Good"

The SDK can be considered high quality when these are true:

- A backend developer can build a chat endpoint with `create_agent`,
  `agent.query` or `session.stream` without importing `runtime.single_agent`.
- A product developer can list sessions, read history, resume/fork a session
  and render stream events without learning checkpoint internals.
- A tool developer can register a typed Python function in under 10 lines for
  low-risk use, and add governance metadata when needed.
- A platform developer can configure providers, stores, timeout/retry,
  permission mode and context policy from one typed SDK config.
- A support engineer can get a redacted support bundle and stable trace summary
  from a run id.
- The public package can be imported without CLI/TUI/demo dependencies leaking
  into SDK runtime.
- New internal runtime features do not require downstream SDK users to consume
  new untyped metadata keys.

## Bottom Line

`agent_driver` is ahead of many small agent runtimes in durability,
observability, governed tools, source evidence and practical chat-demo gating.
Its SDK is promising because the hard primitives are already there.

The remaining work is mostly **API productization and state discipline**:
make sessions explicit, make streaming pleasant, make tools easier, make
context pressure visible early, and turn internal metadata into typed contracts.
That is a good place to be. The engine has enough horsepower; now the dashboard
needs labels that a downstream developer can trust.
