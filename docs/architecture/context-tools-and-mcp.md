# Context Engineering, Tools, And MCP Integration

## Why This Matters

Compaction is only one part of context engineering. Modern agent systems also use context offloading, artifact stores, subagent isolation, tool-result shaping, retrieval of prior work, and careful tool design. Tools are not just developer APIs exposed to the model; they are model-facing interfaces that need ergonomic descriptions, bounded outputs, and security controls.

External references:

- [Anthropic: Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents)
- [Anthropic: Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents)
- [Deep Agents overview](https://docs.langchain.com/oss/python/deepagents/overview)
- [CoSAI: Model Context Protocol security](https://github.com/cosai-oasis/ws4-secure-design-agentic-systems/blob/main/model-context-protocol-security.md)

## Context Engineering Layers

`agent-driver` should model context as a managed runtime resource:

- active prompt context;
- short-term graph state;
- durable thread memory;
- offloaded artifacts/files;
- subagent private context;
- tool results;
- retrieved previous artifacts;
- compaction summaries.

The runtime should track context budgets and context provenance.

## Context Offloading

Deep-agent systems commonly offload large intermediate outputs to a filesystem-like substrate instead of keeping everything in the active prompt.

`agent-driver` should include an artifact/context store protocol:

- write artifact;
- read artifact by id/path;
- patch artifact;
- list artifacts;
- summarize artifact into prompt;
- attach artifact pointer to run output.

Backends:

- in-memory for tests;
- local filesystem for development;
- app-provided object store later.

Artifacts should have:

- id/path;
- mime type;
- size;
- sensitivity label;
- source tool/run id;
- created/updated timestamps.

## Planning And Task State

Long tasks need an explicit plan state. This can start as a general-purpose todo/task tool:

- create/update task list;
- one active task at a time by default;
- statuses: pending, in_progress, blocked, completed, cancelled;
- attach task updates to trace events;
- compact plan state separately from conversation history.

This helps agents maintain direction over long sequences and gives users visible progress.

## Tool Ergonomics

Tool design should optimize for agent use, not only developer convenience:

- clear, verb-first names;
- narrow responsibilities;
- concise descriptions with when-to-use guidance;
- arguments that match user intent;
- output with both machine-readable fields and short human-readable summaries;
- bounded previews plus artifact pointers for large outputs;
- explicit error codes and remediation hints.

Every reusable tool should have:

- manifest entry;
- risk level;
- side-effect class;
- timeout;
- output budget;
- approval policy;
- eval cases.

## Tool Result Budgets

The runtime should enforce:

- max result characters;
- max result structured items;
- preview vs full artifact split;
- summarization policy;
- truncation metadata;
- untrusted content markers.

This prevents tool calls from poisoning or crowding out the prompt context.

Current first-cut implementation:

- `ToolManifest.output_char_budget` configures per-tool summary truncation budget;
- runtime tool executor emits `ToolResultEnvelope` with `truncated` metadata;
- guardrail hooks can block/sanitize tool args/results/final envelope before traces.

## MCP Integration

MCP should be optional, but the architecture should account for it early. MCP can import external tools, resources, and prompts into the engine.

Potential package area:

```text
agent_driver/
  mcp/
    client.py
    discovery.py
    tool_adapter.py
    resource_adapter.py
    security.py
```

MCP tool import should:

- map tool descriptors into `agent-driver` tool manifests;
- mark MCP output as untrusted external content;
- require approval policy per server/tool;
- scope credentials per server/tool;
- log server identity and tool descriptor hash;
- support allowlist of trusted servers.

## MCP Security Baseline

MCP introduces specific risks:

- malicious tool descriptions;
- prompt injection in tool outputs;
- confused-deputy credential abuse;
- compromised local server process;
- secret leakage through headers/env;
- missing audit trail.

Controls:

- explicit server allowlist;
- credential scoping;
- descriptor integrity/hash;
- tool output sanitization;
- approval for side-effecting tools;
- network and filesystem sandbox boundaries;
- full audit chain from user request to external action.

## Workflow vs Agent Choice

The engine should support both deterministic workflows and agentic loops:

- Use workflow when the task path is known and reliability matters most.
- Use agent loop when the model must choose tools or adapt strategy.
- Use handoff/subagent when a branch needs distinct instructions, tools, or isolated context.
- Use agents-as-tools when the parent should retain final synthesis control.

This decision model should be part of documentation and examples, not hidden in code.

## MVP Recommendation

Add early:

- artifact/context store protocol;
- planning/todo tool;
- tool-result preview/artifact split;
- MCP descriptor import design, even if implementation comes later;
- tool security checklist;
- eval cases for tool ergonomics and output budgets.
