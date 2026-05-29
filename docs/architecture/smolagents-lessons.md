# Smolagents Lessons For Agent Driver

## Why This Matters

`smolagents` is not a durable production runtime in the same sense as `agent-driver`, but it has several strong product ideas that are easy to miss when focusing only on LangGraph-style durability:

- a simple step loop with explicit Thought, action, and Observation memory;
- CodeAgent as a first-class agent profile, not only a tool-calling variant;
- model-facing tool contracts that are optimized for successful LLM use;
- a planning prompt that separates known facts, unknown facts, derived facts, and next steps;
- managed agents exposed through the same ergonomic interface as tools.

External references:

- [Building good Smolagents](https://smolagents.org/docs/building-good-smolagents/)
- [Smolagents source tree](https://github.com/huggingface/smolagents/tree/main/src/smolagents)

## What To Adopt

### Agent Profiles, Not One Loop

`agent-driver` should separate the durable runtime from the model action style. The runtime owns checkpoints, events, guardrails, interrupts, storage, and replay. Agent profiles own how a model expresses the next action.

Initial profiles:

- `chat_only`: no tools, memory-aware answer generation;
- `tool_calling`: provider-native tool/function calls;
- `react_text`: textual Thought/Action/Observation loop for models without native tool calls;
- `code_agent`: Python-code action loop with a sandboxed executor and explicit authorized imports.

The CodeAgent profile should be opt-in. It can be powerful for data transformation, multi-tool composition, and local reasoning, but it has stronger sandbox, approval, and trace requirements than ordinary tool calls.

### Versioned Prompt Template Registry

Prompts should be versioned runtime assets, not ad hoc strings embedded in graph builders. The registry should support:

- template id and semantic version;
- required placeholders such as tools, managed agents, authorized imports, custom instructions, and task;
- rendered prompt hash in traces;
- profile compatibility metadata;
- eval fixtures that verify rendered prompts contain required tool and safety sections.

This lets `agent-driver` borrow useful smolagents prompt structure while keeping prompts auditable and replaceable by applications.

### Tool Contracts For The Model, Not Only Python

Smolagents is strict about tool names, descriptions, inputs, output types, and generated code prompts. `agent-driver` should strengthen its tool manifest in the same direction:

- tool name must be stable, descriptive, and valid for the selected profile;
- every argument needs type, description, requirement/default metadata, and validation;
- output should declare an output type and, when possible, a JSON schema;
- tool description should include when to use the tool, expected input format, output shape, and common failure remediation;
- generated model-facing tool documentation should be tested as part of manifest validation.

The docs should explicitly encode the smolagents lesson: fewer, better-designed tools usually beat many tiny ambiguous tools. Deterministic orchestration should group obvious multi-step APIs before asking an LLM to coordinate them.

### Observation Memory And Print/Log Capture

The smolagents CodeAgent loop treats printed intermediate output as Observation memory. `agent-driver` should generalize this as a runtime primitive:

- capture stdout/stderr or tool logs from sandboxed code/tool execution;
- attach observation previews to action-step events;
- offload large observations to artifact storage;
- make observations available to the next model step through bounded context assembly;
- mark external observations as untrusted content.

This complements the existing event log: events are for machines and operators; observations are model-facing context with provenance and budgets.

### Planning As A First-Class Step

Smolagents has a dedicated planning prompt that asks for:

- facts given in the task;
- facts learned so far;
- facts still to look up;
- facts still to derive;
- a high-level plan.

`agent-driver` should not copy the exact prompt as a universal default, but it should adopt the shape. Planning should be an optional profile capability with typed `planning_step` events and persisted planning state. The existing planning/todo tool can be the durable state surface; the planner prompt provides the model-facing update.

### Managed Agents As Tools

Smolagents exposes managed agents through a tool-like callable with `task` and optional `additional_args`. This maps well to `agent-driver` subagents:

- a child agent can be imported into the parent manifest as an `agent_tool`;
- the parent still records child run lifecycle, provenance, cost, and terminal state;
- `additional_args` should become typed attachments/artifact references rather than arbitrary unbounded prompt text;
- child output must be summarized through a stable final-answer contract.

This gives a simple user model while preserving the stronger `agent-driver` child-run contracts.

### Memory Steps, Views, And Replay

Smolagents separates memory into typed steps: task, system prompt, action, planning, and final answer. It can render those steps back into model messages, expose succinct vs full debug views, and replay the agent's trajectory for humans.

`agent-driver` already has durable events and checkpoints, but it should add a reader-friendly memory projection:

- typed memory-step records derived from runtime events;
- full view for debugging and succinct view for prompt/context assembly;
- deterministic replay rendering for CLI, notebooks, and support bundles;
- `summary_mode`-style projection that omits prompt-heavy details while preserving outcomes;
- direct export of action code blocks for CodeAgent debugging.

This should not replace the event log. It is a view over events/checkpoints optimized for model context and operator understanding.

### Step Callback Hooks

Smolagents lets callers register callbacks for specific memory-step classes. `agent-driver` should offer a similar extension point, but with durable runtime constraints:

- callbacks subscribe to typed step/event classes;
- callbacks are best-effort side effects, not part of checkpoint correctness;
- callback failures are traced and policy-controlled;
- built-in callbacks can feed metrics, progress UIs, eval collectors, and support logs.

This gives applications integration hooks without making every app fork the runner.

### Developer Introspection

Smolagents has useful developer ergonomics: rich step logs, token/timing monitor, and an agent tree visualization that shows tools, managed agents, and authorized imports.

`agent-driver` should provide equivalent neutral devtools:

- per-step timing and token counters in local runs;
- CLI replay of a run from persisted events;
- manifest/tree view of graph preset, agent profile, tools, subagents, risk levels, and authorized imports;
- redaction-safe support bundle export for incident review.

These are not production observability exporters, but they make local debugging and documentation much faster.

### Safe Serialization For Executor Boundaries

Smolagents' remote executor path has an explicit safe serializer: JSON-first with type markers, optional pickle only when explicitly allowed, and warnings around insecure fallback.

`agent-driver` should adopt the principle for any sandbox, remote executor, worker, or artifact boundary:

- JSON-safe serialization is the default contract;
- pickle or arbitrary object transfer is opt-in and marked unsafe;
- common rich types can use explicit type markers;
- serialized payloads carry schema/version metadata;
- unsafe deserialization is blocked for untrusted tools and MCP outputs.

This is especially important for CodeAgent, background workers, and cross-process subagents.

### Local Python Executor Guardrails

Smolagents' local Python executor includes concrete guardrails: authorized imports, dangerous module/function checks, dunder access blocking, loop/operation/time limits, output length limits, and final-answer extraction.

`agent-driver` should not assume this is sufficient as a production sandbox, but it is a good minimum contract for an executor adapter:

- authorized import policy;
- forbidden modules/functions and dunder access policy;
- max operations, loop iterations, execution time, and output length;
- persistent code state only within a run/checkpoint scope;
- final-answer extraction as a typed runtime transition;
- clear `interpreter_error` payloads with remediation hints.

The production default should still be conservative: CodeAgent disabled unless an app supplies an acceptable sandbox policy.

### Exec Namespace Pitfall

When embedding Python execution in a sandbox, avoid `exec(code, globals_dict, locals_dict)` with two
different dict objects unless you intentionally want class-body-like scoping behavior. In that mode,
`import` bindings land in `locals_dict`, while names inside user-defined functions are resolved via
module globals (`LOAD_GLOBAL`) and can fail with `NameError` even though the import appeared to succeed.

For REPL- and notebook-like behavior, use one shared namespace for both exec/eval so imports, function
definitions, and later expressions all resolve consistently.

### MCP Structured Output And Lifecycle

Smolagents' MCP client is small but useful: connection lifecycle is explicit, context-manager usage is encouraged, transports are validated, and structured output can preserve MCP `outputSchema`/structured content.

`agent-driver` should include this in the MCP adapter design:

- MCP server connections have explicit lifecycle and cleanup;
- imported tool descriptors include server identity, transport, descriptor hash, and structured output support;
- `outputSchema` maps into `ToolManifest.output_schema`;
- structured content is preferred over text parsing when available;
- legacy text-only MCP tools remain supported but marked less predictable for chaining.

## What Not To Copy

- Do not make Python code execution the default agent loop.
- Do not rely on prompt-only rules for filesystem, shell, network, or credential safety.
- Do not let print output become an unbounded hidden memory channel.
- Do not collapse durable checkpoints into a transient in-process memory object.
- Do not treat managed agents as ordinary tool calls without child-run records.
- Do not permit pickle or arbitrary object deserialization across untrusted executor/tool boundaries.
- Do not treat a restricted Python interpreter as a complete security sandbox.

## Roadmap Implications

The current durable-first roadmap remains right. Smolagents should change the shape of Phase 3 and Phase 6 more than the order:

- Phase 0 should include contracts for agent profiles, action steps, observations, prompt templates, and generated tool documentation.
- Phase 3 should validate tool manifests as model-facing interfaces and introduce tool/profile rendering tests.
- A CodeAgent profile should be added only after tool governance, output budgets, and guardrails exist.
- Phase 6 should expand planning from a todo tool into a planner prompt plus persisted planning events.
- Phase 5 should include local replay/devtool views derived from memory-step projections.
- Phase 7 should define executor serialization, import policy, operation limits, and interpreter-error contracts.
- Phase 9 should model managed agents as a user-friendly facade over explicit subagent runs.
- Phase 10 should map MCP structured output schemas into tool manifests and preserve lifecycle/audit metadata.

## Acceptance Checks

- A fake tool manifest can render provider-native, ReAct, and CodeAgent-facing documentation deterministically.
- A CodeAgent dry-run can execute a safe arithmetic/tool-composition task in a sandbox and persist action/observation events.
- Planning updates are persisted, replayable, and separately compactable from chat history.
- A managed-agent call creates a child run row and returns a bounded parent-visible summary.
- Eval cases catch ambiguous tool descriptions, invalid tool schemas, and profile-incompatible prompts.
- A run can be rendered as full debug memory, succinct model context, and CLI replay from the same persisted events.
- Executor boundary serialization rejects unsafe pickle/object payloads by default.
- MCP tools with `outputSchema` import structured output contracts into the manifest.
- `python` tool can be exposed in regular tool-call profiles with backend abstraction
  (`local`/`docker`/`e2b`/`wasm`) while reusing CodeAgent policy and serialization guards.
- Python tool manifest description should be treated as source of truth for model-visible
  imports/capabilities; system prompt should carry only concise cross-tool sandbox policy.
