# Warning events contract

This note describes the structured `RuntimeEventType.WARNING` events emitted
by the runtime and the domain-neutral projection helper that host applications
can use to bridge them into their own user-facing channels (SSE, WebSocket,
CLI, etc.).

The same family also carries the compaction-related signals
(`compaction_circuit_breaker`) — every WARNING kind the runtime emits goes
through `project_warning_event` so SSE consumers handle one stable
projection shape across all kinds.

## Status

- Implemented: token-pressure warnings emitted from the LLM step (see
  `agent_driver/runtime/single_agent/llm_step.py::_emit_token_pressure_warning`).
- Projection helper: `agent_driver.adapters.project_warning_event`.
- Forward-compatible: unknown warning kinds are silently dropped by the
  projector (returns `None`).

## Why a stable projection contract

Different host applications have their own user-facing vocabulary for context
warnings (warning ids, copy, severity colour, suggestion slugs). The runtime
should not encode any of that vocabulary directly; it should expose enough
structured signals so application adapters can map between them.

The contract guarantees:

1. **Stable signal ids** that survive across minor releases.
2. **Pre-computed severity** so applications do not re-derive it from numeric
   thresholds.
3. **All thresholds and the derived usage ratio** in one payload, so an
   application can render the same message under either soft, compact, or
   blocking pressure without re-querying the runtime.

## Token-pressure warnings

### Event shape

`RunStreamEvent.event == "warning"` with `data`:

```jsonc
{
  "kind": "token_pressure",
  "signal_id": "context_above_soft_threshold" | "context_compact_recommended" | "context_blocking_threshold",
  "severity": "info" | "warning" | "critical",
  "state": "warning" | "compact_recommended" | "blocking",
  "used_tokens_estimate": 8000,
  "remaining_tokens_estimate": 2500,
  "context_window_estimate": 12000,
  "output_token_reserve": 1500,
  "warning_threshold": 7500,
  "compact_threshold": 9000,
  "blocking_threshold": 10500,
  "usage_ratio": 0.6667
}
```

`usage_ratio` is `null` when `context_window_estimate` is zero or missing.

### Stable signal ids

| `state`                | `signal_id`                       | `severity` |
| ---------------------- | --------------------------------- | ---------- |
| `warning`              | `context_above_soft_threshold`    | `warning`  |
| `compact_recommended`  | `context_compact_recommended`     | `warning`  |
| `blocking`             | `context_blocking_threshold`      | `critical` |

The `state` field is the legacy field name used internally by
`agent_driver.context.token_pressure`. The `signal_id` is the canonical
identifier hosts should rely on; `state` is included for backward-compatible
consumers but should not be the source of truth for new integrations.

### When the event is emitted

The LLM step calls `_emit_token_pressure_warning` after each request
assembly. The event is emitted only when `state` is one of the three
non-`ok` levels. There is no built-in deduplication: hosts are expected to
debounce repeats across consecutive turns themselves (a common pattern is
"only re-emit when severity level rises", as the reference ZION integration
does in `should_emit_warning(...)`).

## Projection helper

`agent_driver.adapters.project_warning_event(event)` returns either `None`
(for non-warning events or unknown kinds) or:

```python
{
    "kind": "token_pressure",
    "signal_id": "context_above_soft_threshold",
    "severity": "warning",
    "data": {
        # all fields from the original payload that are recognized
        "state": "warning",
        "used_tokens_estimate": 8000,
        ...
    },
}
```

The helper is intentionally minimal: it does not produce human-readable
messages, suggestion slugs, or any application vocabulary. It guarantees the
shape so adapters can do their own mapping.

### Reference application mapping

A host such as ZION typically maps the projection into its own SSE event
shape:

```python
projection = project_warning_event(stream_event)
if projection is None:
    return None

WARNING_VOCAB = {
    "context_above_soft_threshold": {
        "warning_id": "context_above_80pct",
        "message": "Context is above ~80% of the window; compaction may run soon.",
        "suggestion": "near_hard_threshold",
    },
    "context_compact_recommended": {
        "warning_id": "context_above_soft",
        "message": "Context is above the compact threshold; consider artifact-first reads.",
        "suggestion": "consider_compact",
    },
    "context_blocking_threshold": {
        "warning_id": "context_near_hard",
        "message": "Context usage is at or above the hard summarization threshold.",
        "suggestion": "near_hard_threshold",
    },
}
vocab = WARNING_VOCAB[projection["signal_id"]]
yield {
    "type": "context_warning",
    "warning_id": vocab["warning_id"],
    "level": projection["severity"],
    "message": vocab["message"],
    "suggestion": vocab["suggestion"],
    "detail": projection["data"],
}
```

The vocabulary table belongs to the host, not the runtime.

## Compaction outcome events

`RuntimeEventType.MEMORY_COMPACTED` events carry a stable
`outcome: "skipped" | "successful" | "failed"` field plus a
`compaction_state` snapshot (`consecutive_failures`, `failure_limit`,
`circuit_breaker_open`, `lock_active`). Host applications increment
runtime metrics on every emission without parsing the variant-specific
fields:

```python
def on_memory_compacted(event):
    payload = event.data
    outcome = payload["outcome"]           # skipped|successful|failed
    mode = payload["mode"]                 # session_memory|llm_full|partial|...
    metrics.compaction_outcome(outcome=outcome, mode=mode)
    state = payload["compaction_state"]
    if state["circuit_breaker_open"]:
        # circuit-breaker is open after this attempt
        metrics.compaction_circuit_breaker(state="open")
```

When the circuit breaker transitions from closed to open within a
compaction attempt, the runtime additionally emits a
`RuntimeEventType.WARNING` event with `kind="compaction_circuit_breaker"`
so hosts can fire a single alert per transition (instead of polling
`compaction_state` after every attempt).

## Adding new warning kinds

To add a new warning kind (e.g., `kind="tool_budget_exceeded"`):

1. Emit a `RuntimeEventType.WARNING` event with the kind tag, a stable
   `signal_id`, a pre-computed `severity`, and structured data fields.
2. Extend `project_warning_event` in `agent_driver/adapters/warnings.py` to
   recognize the new `kind` and copy the relevant fields into `data`.
3. Document the kind in this file (event shape, signal ids, severity
   mapping, emission conditions).
4. Add unit tests in `tests/adapters/test_warning_projection.py` and an
   emission test in the relevant `tests/runtime/test_*_emission.py`.

Hosts that have not yet been updated will keep working — the projector
returns `None` for unknown kinds, and `RunStreamEvent` consumers that only
react to recognized projections gracefully ignore the rest.
