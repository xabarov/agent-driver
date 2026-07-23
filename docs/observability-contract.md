# Observability contract (observer / middleware / correlation / redaction)

Status: **v1** (epic 037). Delta on epic 010 (the lifecycle-hook plane exists;
this doc gives it contractual maturity). Companion: `phoenix-openinference-trace-contract.md`
(span shapes) and `runtime-metadata.md` (metadata-key inventory).

Reference anchors: hermes `docs/observability/README.md`, `docs/middleware/README.md`,
`agent/redact.py`.

---

## 1. Observer vs middleware — two contracts, two versions

The single `RunLifecycleHook` Protocol (`agent_driver/runtime/lifecycle_hooks.py`)
carries methods of **two contractual kinds**. `agent_driver/contracts/observability.py`
classifies every method; `tests/contracts/test_observability_contract.py` locks the
split in both directions, so adding a hook method forces a conscious decision.

| Kind | Methods | Return | Discipline |
|------|---------|--------|-----------|
| **Observer** (`agent_driver.observer.v1`) | `on_run_start`, `after_llm_response`, `on_error`, `on_run_completed` | `None` | Read-only. MUST NOT change run behaviour. A raising observer is isolated + logged (fail-open), never propagated. |
| **Middleware** (`agent_driver.middleware.v1`) | `before_llm_request`, `on_finalize`, `on_tool_evidence` | a value that alters the run | Behavior-changing: replace the request / request a revision / finalize early from tool evidence. |

Version constants: `OBSERVER_SCHEMA_VERSION` / `MIDDLEWARE_SCHEMA_VERSION`.
`describe_observability_contract()` returns the versions + classification for a
support bundle so a consumer pins the schema it parses without importing internals.

Existing epic-010 hooks are **classified, not migrated** — no behavior change.

## 2. Correlation model (no clock-skew matching)

Span↔run matching must not depend on wall-clock proximity (the Phoenix container
has observed clock skew). The trace id is a **deterministic function of the run**:

```
deterministic_trace_id(run_id, attempt_id) == f"trace_{sha1('run_id:attempt_id')[:16]}"
```

- Both the live emit path (`SingleAgentStepMixin._emit`) and the deterministic
  trace export (`observability/trace_builder.py`) derive the trace id from this
  ONE function, so **every `RuntimeEvent` carries the same `trace_id` its span
  will have** — correlation by construction.
- `correlation_ids(run_id, attempt_id, thread_id=?)` returns the canonical bundle
  (`run_id`, `attempt_id`, `trace_id`, optional `thread_id`). Tool (`tool_call_id`)
  and subagent parent↔child ids remain on their own contracts (`ToolTrace`,
  `SubagentRun`) and in event payloads.

## 3. Payload sanitization (bounded + secret-safe)

`observability/redaction.py::sanitize_observer_payload(value) -> (cleaned, RedactionInfo)`
is the seam a host plugs its PII redaction into **before export**:

- **Bounds** (hermes `_hook_jsonable`): `MAX_DEPTH=8`, `MAX_STRING=8000`,
  `MAX_SEQUENCE=200`; over-limit strings truncated with a `...[truncated N chars]`
  marker, over-limit collections emit `{"_truncated_items": N}`, bytes → `<N bytes>`,
  depth cap → `<max-depth>`; arbitrary objects normalize via `model_dump` → `str()`.
- **Secret keys** (hermes `_is_sensitive_hook_key`): values under `api_key`,
  `authorization`, `cookie`, `token`, `password`, `secret`, `*_api_key`, … are
  replaced with `<redacted>` (exact-match + suffix, so `token_count`/`session_id`
  survive). Never raises — a bad leaf degrades, it does not fail the run.
- The returned `RedactionInfo` (applied / policy / `redacted_fields` names-only /
  sensitivity / `truncated`) is what `RuntimeEvent.redaction` now carries (it was a
  dead field before this epic — unreachable through `new_runtime_event`).

This is structured-payload sanitization. Free-text scrubbing (vendor-key prefixes,
JWTs, phone numbers) is a separate concern in `llm/sanitize.py` +
`context/compaction/sanitizers.py`; content-level trace redaction is the
`phoenix_io_redacted()` / `_is_content_key` boolean seam in `observability/openinference.py`.

## 4. Cheap uninstrumented path (`has_hook`)

`runtime/lifecycle_hooks.py::has_hook(hooks, method_name)` (hermes `PluginManager.has_hook`):
`True` only when a hook **overrides** that method vs the `BaseRunLifecycleHook` no-op.
A caller about to assemble an expensive observer payload gates on it, so the default
path (no subscriber) pays nothing. Existing cheap-path precedents it composes with:
`_emit_if_slow` (only emit when a hook was slow) and `oi_span`/`get_otel_tracer`
returning no-ops when tracing is off.

## 5. Versioning + inventory discipline

- Version strings live in `contracts/observability.py` (`agent_driver.observer.v1` /
  `.middleware.v1`), matching the existing `schema_version` field style.
- No new `context.metadata[...]` literal keys are introduced (the emit path carries
  correlation on the event, not in `context.metadata`), so the discipline-008
  inventory (`docs/runtime-metadata.md` + `tests/runtime/test_runtime_metadata_inventory.py`)
  is unaffected.

## Not in scope

Provider catalog / routing (epic 012); UI status surfaces (epic 025 — a consumer,
not part of this contract); a full string-keyed plugin manager (our typed
`RunLifecycleHook` Protocol stays — this epic matures its contract, it does not
replace the plane).
