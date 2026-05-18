# Human Review, Interrupts, And Guardrails

## Why This Matters

Agents become risky when they can call tools, write files, execute shell commands, access private data, or trigger external APIs. Tool allowlists are necessary but not enough. A production engine needs explicit interrupt, approval, and guardrail contracts.

External references:

- [LangGraph human-in-the-loop / interrupts](https://docs.langchain.com/oss/python/langgraph/interrupts)
- [OpenAI guardrails and human review](https://developers.openai.com/api/docs/guides/agents/guardrails-approvals)
- [Anthropic: Building effective agents](https://www.anthropic.com/engineering/building-effective-agents)

## Interrupt Model

`agent-driver` should support first-class interrupts:

- agent asks for clarification;
- runtime pauses before high-risk action;
- tool policy requires approval;
- guardrail blocks and requests human decision;
- app pauses for external workflow state.

Interrupt payload should include:

- `interrupt_id`;
- `run_id`;
- `checkpoint_id`;
- `reason`;
- `risk_level`;
- `proposed_action`;
- `tool_name`;
- `tool_args_preview`;
- `editable_fields`;
- `allowed_responses`;
- `expires_at`;
- `audit_metadata`.

Resume command should support:

- approve;
- reject;
- edit arguments;
- provide clarification;
- cancel run;
- patch state and continue.

## Approval Policies

The tool manifest should include approval metadata:

- risk level;
- side-effect class;
- required approval mode;
- allowed scopes;
- credential scope;
- max cost;
- max runtime;
- data sensitivity.

Approval modes:

- `never`: safe pure/read-only tool;
- `on_policy_match`: approve only when policy says so;
- `always`: every call requires approval;
- `step_by_step`: approval after every graph node for audited workflows.

The engine should let applications define policy functions:

```text
PolicyDecision = allow | deny | interrupt
```

with a structured reason and optional remediation hint.

## Guardrail Pipeline

Guardrails should be separate from tool execution:

- input guardrails before model call;
- prompt/context guardrails before LLM invocation;
- tool argument guardrails before tool execution;
- tool result sanitization before returning data to the model;
- output guardrails before final answer leaves the engine.

This separation avoids burying policy inside individual tools.

## Prompt Injection And Tool Output Safety

Tool output must be treated as data, not instructions. The engine should provide:

- tool-result wrappers that clearly separate content from instructions;
- sanitizers for HTML/Markdown/script content;
- metadata marking untrusted external content;
- prompt templates that tell models how to interpret untrusted data;
- optional classifiers for prompt-injection patterns.

For MCP tools, this is especially important because tool descriptions and outputs can become attack surfaces.

## Redaction And Sensitive Data

Guardrails should support:

- input redaction before tracing;
- output redaction before tracing;
- per-span redaction policy;
- secret-pattern detection;
- PII policy hooks;
- artifact-level sensitivity labels.

Redaction must run before data reaches Langfuse/Phoenix/LangSmith/OpenTelemetry exporters.

## UX Contract

Applications need enough information to render approvals:

- concise human-readable summary;
- exact proposed tool call;
- risk explanation;
- diff for file changes;
- command preview for shell calls;
- destination URL for HTTP calls;
- data access summary.

For developer tools, shell/filesystem approvals should feel like reviewing a patch or command, not like approving opaque JSON.

## MVP Recommendation

Implement this before broad tool support:

- `InterruptRequest` and `ResumeCommand` contracts;
- approval-aware tool manifest fields;
- guardrail pipeline with no-op defaults;
- built-in policies for shell/filesystem/HTTP tools;
- persisted pending interrupt in checkpoint state;
- tests for approve/reject/edit/resume flows.
