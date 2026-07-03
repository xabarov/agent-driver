# Phoenix OpenInference Trace Contract

Status: draft contract for policy-supervision and host adapters.

Agent-driver traces sent to Phoenix should optimize for human debugging, not
only raw telemetry export. Use OpenInference attributes consistently so Phoenix
renders the trace tree and right panel as agent, LLM, tool and guardrail spans.

## Span Hierarchy

One normal run should have a single root:

```text
agent.run                         AGENT
  policy.<policy-id>              GUARDRAIL
  subagent.<name>                 AGENT
    llm.<purpose>                 LLM
    tool.<tool-name>              TOOL
    llm.final                     LLM
```

Rules:

- The root run span is `AGENT` and carries run/session/app metadata.
- Subagents are nested `AGENT` spans, not root-level siblings.
- Provider calls are `LLM` spans under the active agent/subagent span.
- Tool executions are `TOOL` spans under the agent/subagent or LLM planning
  span that caused them.
- Policy/supervision checks that affect the run are `GUARDRAIL` spans.
- Normal runs should not leave orphan `LLM` or `TOOL` spans at trace root.

## Required Attributes

Every visible span:

- `openinference.span.kind`
- `input.value` / `input.mime_type` when useful
- `output.value` / `output.mime_type` when useful

`LLM` spans:

- `llm.model_name`
- `llm.provider`
- `llm.invocation_parameters`
- `llm.input_messages.*.message.role`
- `llm.input_messages.*.message.content`
- `llm.output_messages.*.message.role`
- `llm.output_messages.*.message.content`
- `llm.finish_reason`
- `llm.token_count.prompt`
- `llm.token_count.completion`
- `llm.token_count.total`
- `llm.cost.total` when known

`TOOL` spans:

- `tool.name`
- `tool.description` when available
- `tool_call.id` when available
- `tool_call.function.name`
- `tool_call.function.arguments`
- redacted result mirrored to `output.value`

`GUARDRAIL` spans:

- `policy.id`
- `policy.action`
- selected reason and redacted metadata when available

## Redaction

Never export API keys, auth headers, raw private workbook dumps, full fetched
pages or oversized prompts. Prefer short summaries, artifact ids, source ids,
run ids and explicit `redacted=true` payload markers.

## Verification

- In-memory tests must assert span kind, parent-child ids and critical
  right-panel attributes.
- Phoenix smoke must emit the hierarchy above without live provider spend.
- Live OpenRouter/product validation must add Phoenix UI screenshots before the
  Phase I trace-quality gate can close.
