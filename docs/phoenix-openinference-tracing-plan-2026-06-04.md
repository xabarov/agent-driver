# Phoenix / OpenInference tracing — engine plan (2026-06-04)

## Problem

Phoenix renders our traces as **gray, flat, JSON-like rows** instead of a rich
agent trace (colored span kinds, nested agent→LLM→tool hierarchy, Input/Output
panels, token counts, cost, error status). Reference of a *good* trace:
`excel_ai/docs/backlog/bugs/инфографика/good-traces.png` — spans labelled
`llm_call`, `tool …`, `agent_… run`, with Input/Output/Status panels.

**Root cause:** our spans don't follow the **OpenInference semantic conventions**.
Today only the host (excel_ai) opens a few spans with ad-hoc `excel_ai.*`
attributes; the **engine emits no spans at all** for the run loop, LLM calls,
tools or subagents. Phoenix's AI-aware rendering keys entirely off OpenInference
attributes (`openinference.span.kind`, `input.value`, `output.value`, `llm.*`,
`tool.*`), so without them every span is a generic gray box.

Only `openinference-semantic-conventions` (the constants pkg) is installed — no
auto-instrumentors, and the LLM client is a custom `httpx` call, so
**manual instrumentation in the engine is the path** (auto-instrumentors can't
see our loop/tools).

## Split

The run loop, LLM calls, tool execution and subagents are **engine-owned and
generic to every consumer** (excel_ai, deep-research, chat-demo) → instrument them
**here, in agent-driver**. Only domain attributes (workbook/sheet, excel tool
specifics) belong in excel_ai — see
`excel_ai/docs/backlog/phoenix-tracing-excel-specific-2026-06-04.md`.

---

## OpenInference attribute reference (target)

| Concern | Attribute keys |
|---|---|
| Span kind (REQUIRED) | `openinference.span.kind` ∈ {`AGENT`,`CHAIN`,`LLM`,`TOOL`,`RETRIEVER`,`EVALUATOR`,`GUARDRAIL`} |
| I/O | `input.value`, `input.mime_type`, `output.value`, `output.mime_type` |
| LLM | `llm.model_name`, `llm.provider`, `llm.system`, `llm.invocation_parameters` (JSON), `llm.token_count.{prompt,completion,total}`, `llm.input_messages.<i>.message.{role,content}`, `llm.output_messages.<i>.message.{role,content}` |
| Tool | `tool.name`, `tool.description`, `tool.parameters`/`tool.json_schema`, `tool_call.id`, `tool_call.function.{name,arguments}` |
| Status | OTel span status `OK`/`ERROR` + `record_exception`; Phoenix shows it as "Status Description" |
| Hierarchy | native OTel parent/child (spans opened inside the parent's active context) |
| Cost | derived by Phoenix from `llm.token_count.*` + a per-model price (project config), OR emit a cost attribute |

---

## Workstream A — engine OpenInference emitter (foundation)

1. **`agent_driver/observability/openinference.py`** — thin helpers over the
   existing `get_otel_tracer`/`start_otel_span`:
   - `oi_span(name, *, kind, attributes, parent_context=None)` context manager that
     sets `openinference.span.kind` and yields the span.
   - `set_io(span, input=…, output=…)` → `input.value`/`output.value` (+ mime type,
     JSON when dict).
   - `set_llm(span, model, provider, params, in_messages, out_messages, usage)`.
   - `set_tool(span, name, description, args, result, call_id)`.
   - `record_status(span, ok: bool, description: str | None)` → OTel status + (on
     error) `span.record_exception` / status description.
   - All no-op when tracing is off; never raise. Reuse the kwargs-filtering
     `register()` shim already in `observability/phoenix.py`.
2. **Tracer-provider ownership**: today excel_ai calls `register()`. Move the
   provider setup behind `RunnerConfig`/`PhoenixTracingConfig` so the engine can
   open spans whether or not the host configured Phoenix (graceful no-op otherwise).

## Workstream B — run / agent / chain spans

- Open one **`AGENT`** (or `CHAIN`) span per runtime run in the run lifecycle
  (`runtime/single_agent/lifecycle/steps.py` `_execute_run_started` … finalize).
  - `input.value` = the user turn / messages; `output.value` = final answer.
  - status ERROR + description when the run ends non-completed.
  - This becomes the trace root for native nesting — **replaces excel_ai's
    contextvar/copy_context nesting hack**.
- Subagents (`subagents/`, `tool_stage/subagent_execution.py`) open nested
  `AGENT` spans under the parent run's context.

## Workstream C — LLM spans (the headline gap)

- Wrap each provider call in `agent_driver/llm/base.py` (around the
  `async with self.build_async_client(...)` request at ~247, both streaming and
  non-streaming) in an `LLM` span:
  - `llm.model_name`, `llm.provider`, `llm.system` (from provider/model id).
  - `llm.invocation_parameters` = JSON of temperature/max_tokens/tool_choice/etc.
  - `llm.input_messages.<i>.*` from the request messages; `llm.output_messages.<i>.*`
    from the response/assembled stream.
  - `llm.token_count.{prompt,completion,total}` from the existing `UsageSummary`
    (`llm/streaming.py`, `llm/contracts.py`) — **this is what unlocks token counts
    AND cost in Phoenix**.
  - status from HTTP result; on provider error, record the exception (so denied/
    failed calls show a red Status Description like the reference screenshot).
- Streaming: open the span before the request, close on stream finish (when
  `UsageSummary` is known).

## Workstream D — tool spans (fixes "denied" being opaque)

- In the governed executor (`tools/executor/governed.py`), wrap each tool call in a
  `TOOL` span:
  - `tool.name`, `tool_call.id`, `tool_call.function.{name,arguments}`,
    `input.value` = args, `output.value` = result summary.
  - On DENY / `tool_handler_error` / failure → status ERROR + description = the
    block reason (e.g. the SQLAlchemy "concurrent operations" message that made
    `chart_vegalite` get denied). **Today this is invisible in Phoenix** — the user
    only saw "denied" with no reason; a TOOL span with status description surfaces it.
  - GUARDRAIL span (or an event) for the tool-gate Ask/Deny decision.

## Workstream E — cost

- `cost_per_1k_tokens` already exists in the provider config (`llm/base.py:148`),
  defaulting to `0.0` → Phoenix shows **$0**. Two fixes, do both:
  1. Emit `llm.token_count.*` (Workstream C) so Phoenix can compute cost from its
     per-model price table.
  2. Let the host configure real per-model prices (excel_ai passes qwen's price);
     optionally emit a `llm.cost.total` attribute when known.

## Workstream F — wiring & rollout

- Env-gated (reuse `EXCEL_PHOENIX_*` / a generic `AGENT_DRIVER_OTEL_*`); default
  off; zero overhead and never raises when disabled.
- Add `openinference-semantic-conventions` constants usage; no new heavy deps.
- Tests: assert spans carry the right `openinference.span.kind` + I/O + token
  attributes (use an in-memory OTel span exporter).
- Migration: once B lands (engine opens the root + nests natively), excel_ai drops
  its `_run_parent_context`/`copy_context` workaround.

## Sequencing

A → C+D (the two highest-value: LLM and tool spans give kinds + I/O + tokens +
error status) → B (root/nesting, replaces the host hack) → E (cost) → F (rollout).
Each workstream is independently shippable and visibly improves the trace.
