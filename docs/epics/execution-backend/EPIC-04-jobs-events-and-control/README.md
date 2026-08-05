# EPIC-04 — Execution Jobs, Events, Control, and Recovery

Status: blocked on EPIC-03.

## Outcome

Long-running backend operations have stable identities, bounded ordered events,
reconnectable snapshots, truthful controls, result fencing, and explicit
teardown receipts. A lost HTTP/WebSocket/process response does not cause Agent
Driver to blindly repeat an unknown side effect.

## Why this epic exists

A blocking command callback is adequate for short local calls but not for
remote execution that can outlive a transport connection. Reliable operation
requires separation between dispatch, observation, control, terminal result,
and environment teardown.

## Baseline to inspect

- EPIC-01 through EPIC-03 implementation/handoffs
- runtime `RuntimeEvent`, `RunStreamEvent`, cursor/replay and checkpoint code
- `RunAbortHandle`, `ToolCancellation`, live-message controls and Stop semantics
- `RESULT_FENCED`, attempt/generation fencing, and terminal-phase contracts
- tool progress, trace, result-envelope, artifact-spill, and timeout code
- durable lifecycle snapshot/attach patterns

## In scope

1. Add execution handle, execution generation, terminal snapshot, event page,
   event cursor, control request/receipt, and teardown receipt contracts.
2. Split remote-capable execution into idempotent start/lookup, observe/replay,
   snapshot, control, and terminal collection semantics.
3. Keep a blocking convenience operation only as an adapter over the lifecycle,
   with the same bounds and failure semantics.
4. Project bounded backend events into existing runtime progress/trace/event
   surfaces without exposing raw remote payloads or duplicating terminal state.
5. Reconnect from the last committed cursor, detect gaps/compaction, query a
   snapshot, and continue without redispatching an indeterminate operation.
6. Fence results/events from stale run attempts, lease generations, execution
   generations, and duplicate terminal deliveries.
7. Integrate run abort/Stop with the strongest supported backend control and
   report accepted/applied/terminal/teardown facts separately.
8. Define timeout behavior for queue, start, idle output, total execution,
   control application, and teardown.
9. Record queue/acquire/ready/start/first-output/control/terminal/teardown
   timings and typed reason codes.
10. Support process restart/recovery using only durable safe references and the
    backend snapshot API.

## Non-goals

- Promising exactly-once side effects when the backend cannot prove them.
- Requiring pause/resume/signals/PTY/SSH. These are optional capabilities.
- Turning terminal output into instructions or bypassing tool governance.
- Owning the transport protocol used by an external backend service.
- Treating a cancelled Agent Driver run as proof that remote processes or the
  environment have been destroyed.

## Design constraints

- Event identity is stable and replay is duplicate-tolerant.
- Conflicting content for the same execution generation and sequence is a
  backend protocol violation.
- Output bounds apply before event persistence and model projection.
- Start carries an idempotency key; lookup can resolve a lost start response.
- Indeterminate dispatch is explicit and blocks automatic redispatch of a
  potentially mutating operation.
- Cancellation strength is capability-driven and receipt-backed.
- A terminal execution result does not imply a released lease.
- Late fenced data may be recorded diagnostically but cannot become a normal
  tool observation or overwrite a newer result.

## Work packages

### A. Job and event contracts

Implement validated handles, lifecycle states, event pages/cursors, snapshots,
controls, receipts, reason codes, redaction, and bounds.

### B. Runtime observation bridge

Drive progress and terminal collection without blocking the entire runtime
control path. Preserve tool call identity and map backend data into existing
runtime events/traces exactly once.

### C. Reconnect and fencing

Persist the safe handle/cursor needed for recovery. Handle duplicates, gaps,
compaction, stale generations, lost dispatch replies, and conflicting events.

### D. Stop and teardown

Map abort to supported control, observe application, and request lease cleanup
according to ownership. Keep run terminal, execution terminal, and teardown
state independently visible.

### E. Timing and failure injection

Expose stage timings and prove queue timeout, idle timeout, total timeout,
transport loss, control timeout, teardown failure, and late-result behavior.

## Acceptance scenarios

1. A long command streams multiple bounded progress/output events and one
   terminal result through a real run.
2. Reconnect after event N replays N+1 onward without duplicate model
   observations; a compacted history uses the snapshot path.
3. Losing the start response then looking up by idempotency key finds the same
   execution. An unresolved result becomes `indeterminate` and is not rerun.
4. A duplicate terminal event is idempotent; conflicting terminal content is a
   typed protocol violation.
5. A late result from an old attempt/lease/execution generation is fenced and
   cannot commit a tool result.
6. Stop against a cooperative-only backend reports request/acceptance and does
   not claim process-tree or environment teardown.
7. Stop against a backend that proves hard teardown records separate applied,
   execution-terminal, and teardown-confirmed receipts.
8. Queue, idle, execution, control, and teardown timeouts produce distinct
   reason codes and timings.
9. Backend event text, errors, and artifacts are bounded and redaction-safe
   before persistence or model exposure.
10. Runtime restart restores the safe handle/cursor and resolves terminal state
    without redispatch.
11. Existing local blocking behavior remains compatible through the adapter.

## Definition of done

- Remote lifecycle semantics are proven under duplicate, loss, delay, restart,
  cancellation, and stale-generation tests.
- Stop/teardown claims are precise and capability-backed.
- Runtime events, traces, checkpoints, snapshots, and docs agree.
- Full default suite and touched-module quality checks pass.
- SemVer impact and EPIC-05 predecessor evidence are recorded.

