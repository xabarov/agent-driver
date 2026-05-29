# Observability — span attribute hooks

This note describes how a host application attaches domain-specific
attributes to spans exported by `OpenTelemetryPhoenixTraceExporter` and
`LangfuseTraceExporter` without subclassing the exporter or forking the
trace contract.

## Motivation

The runtime emits structured `TraceSpan` rows (per event) and assembles them
into a `TraceExport`. Host applications usually need to attach extra
attributes per span — tenant ids, scan profile ids, budget status, recon
state markers — so operators can filter or group traces in Phoenix/Langfuse
without losing the runtime's deterministic structure. A subclass-based
extension would force every host to either fork the exporter or maintain a
shim; a constructor-injected callable keeps the runtime contract clean.

## The resolver protocol

```python
from agent_driver.observability import (
    OpenTelemetryPhoenixTraceExporter,
    SpanAttributeResolver,
    TraceExport,
    TraceSpan,
)


def host_attributes(span: TraceSpan, payload: TraceExport) -> dict[str, str | int | float | bool]:
    return {
        "host.tenant_id": payload.metadata.get("tenant_id", ""),
        "host.run_seq": span.seq,
        "host.event_type": span.event_type,
    }


exporter = OpenTelemetryPhoenixTraceExporter(span_attribute_resolver=host_attributes)
```

The same resolver shape works for `LangfuseTraceExporter`. A host can share
one resolver across both sinks.

## Safety guarantees

The resolver runs inside an isolation wrapper:

1. **Type filtering.** Keys that are not strings are dropped. Values that
   are not `str | int | float | bool` are dropped. This keeps the
   downstream SDK from receiving unexpected payloads (Python dicts, custom
   objects, etc.) that would either crash or be silently mishandled.
2. **Error isolation.** If the resolver raises, the failure is caught and
   the per-span attributes are treated as empty. The exporter still
   finishes the export and records a deduplicated error tag in the
   returned `TraceSinkResult.metadata["custom_attribute_resolver_errors"]`.
3. **Non-dict returns.** A resolver that returns a non-`dict` payload (a
   list, a tuple, a string, …) is treated like an exception with the tag
   `"resolver_invalid_type"`.

## Diagnostics surface

When a resolver is provided, `TraceSinkResult.metadata` carries:

- `custom_attribute_count` — total number of valid resolved attributes
  across all spans;
- `custom_attribute_spans` — number of spans that received at least one
  attribute;
- `custom_attribute_resolver_errors` — deduplicated list of error tags
  (only present if at least one failure occurred).

When no resolver is provided, none of those keys appear — existing
behavior is unchanged.

## When the resolver runs

The resolver runs on every export call, regardless of whether the
underlying OTLP/Langfuse SDK is installed. This is deliberate: hosts can
verify the attribute pipeline in dependency-free CI before wiring the real
SDK in staging/production.

## Attribute naming

The runtime does not enforce a namespace prefix on resolver keys, but the
OpenTelemetry semantic conventions recommend dot-separated lowercase names
(`host.tenant_id`, `host.budget_status`, `host.recon_state.subdomain_count`).
Hosts that ship multiple agents through one Phoenix/Langfuse instance should
keep a stable application-scoped prefix to avoid collisions.

## Future: real OTLP wiring

Today the exporters only validate dependency availability and aggregate
attribute counts in the result metadata; they do not yet push spans to a
real OTLP collector or Langfuse instance. When real wiring lands, the
resolver attributes will be attached to each span via
`opentelemetry.sdk.trace.Span.set_attribute(...)` (or the equivalent
Langfuse trace-update call). The contract documented here is the stable
extension point that survives that transition.
