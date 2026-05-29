# Streaming Trace Review Checklist

Use this checklist for PRs that touch runtime streaming, `RunStreamEvent`,
SSE/CLI adapters, or SDK `stream(...)` behavior.

## Event ordering

- `run_started` appears before LLM/tool progress events.
- `token_delta` events are monotonic by `seq`.
- `token_delta` is emitted before `llm_call_completed` for the same call.
- terminal event (`run_completed` / `run_failed` / `run_cancelled`) is present.

## Token delta and terminal equivalence

- Streamed run emits durable `token_delta` events.
- Final `AgentRunOutput.answer` equals concatenated streamed text.
- Stream and non-stream modes return equivalent terminal output for same provider fixture.

## Reconnect/backfill

- Adapter parses reconnect cursor (`Last-Event-ID` or `after_seq`).
- Backfill returns only events with `seq > after_seq`.
- SSE envelope preserves `stream_id` as SSE `id`.
- Live stream path skips duplicates at or before reconnect cursor.

## Contract hygiene

- `RunStreamEvent` fields include `schema_version`, `source`, and stable `stream_id`.
- Event `data` payload is JSON-serializable.
- Optional retry hint is used only when adapter/client needs it.

## Failure semantics

- Failure before first token emits terminal failure event.
- Failure after partial tokens keeps previously emitted deltas and still fails terminally.
- Checkpoint behavior is explicit and tested (no silent partial-success state).
