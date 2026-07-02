# Runtime Overview

`agent-driver` centers on `SingleAgentRunner`: a durable single-agent loop that
builds an LLM request, streams or receives a response, executes governed tools,
updates context, and persists checkpoints/events after meaningful steps.

## Main Pieces

- `agent_driver.contracts` defines run input/output, messages, tool traces,
  interrupts, control commands, and context contracts.
- `agent_driver.llm` normalizes provider responses and streaming chunks.
- `agent_driver.runtime` owns the runner, checkpoint/event stores, resume,
  compaction hooks, planning reminders, and tool-stage loop control.
- `agent_driver.tools` owns manifests, registry, tool packs, policy evaluation,
  guarded execution, and built-in tools.
- `agent_driver.context` owns planning state, artifacts, projections,
  compaction helpers, and session-memory related state.
- `agent_driver.subagents` owns durable child/group rows, mailbox helpers,
  background scheduling, and parent/child handoff.

## Storage

Runtime state is intentionally store-backed:

- checkpoints keep resumable run state;
- event logs keep typed event history for replay and UI projection;
- optional SQLite/Postgres stores are used where durability matters;
- in-memory stores remain useful for tests and fake/demo scenarios.

The usual app path is:

1. build store config from env;
2. preflight the store;
3. create a runtime store bundle;
4. inject stores into `SingleAgentRunner`.

## Tool Loop

Tools are not raw functions exposed directly to the model. Each tool has a
`ToolManifest` describing schema, risk, side effect, approval mode, output
budget, and prompt-facing description. The governed executor applies policy,
guardrails, interrupts, output limits, and structured error envelopes.

Important runtime guards today:

- `RunnerConfig.default_max_steps` defaults to `80` when a run omits
  `AgentRunInput.max_steps`; set it to `None` only for intentionally unbounded
  loops;
- `RunnerConfig.budget_grace_enabled` opens one bounded no-tools final-answer
  window after soft step/tool budgets, so partial evidence can still produce a
  model-authored answer;
- `RunnerConfig.defer_primer` can surface a small set of relevant deferred
  tools before a step; `keyword_relevance_primer()` provides a generic
  name/description overlap policy, while `tool_search` remains the fallback;
- force planning can block side-effecting tools until an approval plan exists;
- deliverable requests can deny clarification/approval tools for that turn;
- after substantive data tools, explicit deliverable turns can force a final
  answer with `tool_choice=none`;
- chat-mode reminders keep planning and deliverable mode visible to the model.

### Envelope ↔ trace ordering invariant

For every tool stage the governed executor produces two parallel, **index-aligned**
lists in a single `GovernedExecutionResult`: `envelopes[i]` is the result payload for
the call whose status is `traces[i]`. `result.append(...)` only ever appends to both
together, so the 1:1 ordering holds by construction — there is no key to join on; the
index *is* the join.

This invariant propagates verbatim into the run output and events:

- `AgentRunOutput.metadata["tool_results"][i]` ↔ `metadata["tool_trace"][i]`
  (both extended together by `ToolLoopState.append_stage_outputs`, in stage order
  across all stages of the run);
- `AgentRunOutput.tool_trace[i]` mirrors the same row;
- the `TOOL_CALL_COMPLETED` event's `statuses[i]` is `result.traces[i].status`.

Consumers (FE timeline, eval harness, Phoenix exporter) should **zip by position**
rather than cursor-walk or match on tool name — positional pairing is the contract.

A tool trace status is one of `completed` / `failed` / `denied` / `timed_out`
(`STARTED` is transient). Two ways a call ends `FAILED` without raising: a tool whose
manifest sets `success_field` self-reports failure (see `ToolManifest.success_field`),
and a `code_action` whose body raises a non-`CodeExecutionError` (mapped to a redacted
`code_runtime_error`). In both cases the matching `envelopes[i]` carries the `error`
payload, so the positional pairing stays honest.

## Events And Replay

The runtime emits typed events for run lifecycle, LLM chunks, tools, planning,
interrupts, steering controls, subagents, warnings, and terminal output. The
chat demo consumes these events over SSE and can replay persisted sessions.

## Provider Diagnostics

The provider layer exposes redaction-safe route diagnostics before any live
request is made. `ProviderRouteProfile` records request-shaping facts for a
provider/model/base-url family: tool-call support, forced tool-choice support,
strict JSON support, reasoning support, request-id expectations, streaming, and
token-field hints. `ProviderPreflightResult` wraps those facts with the
deterministic request-shape policy and any downgrade notes.

Use these payloads in support bundles, chat-demo provider status, live-probe
artifacts, and downstream harnesses before spending provider tokens. The data
must stay safe to persist: no API keys, auth headers, or raw base URLs.
