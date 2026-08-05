# EPIC-04 Handoff — Execution Jobs, Events, Control, and Recovery

**Date:** 2026-08-05
**Status:** Delivered. Version `0.8.0`. **Final epic of the execution-backend package.**
**Branch:** `feat/execution-backend-epic04`

## What shipped

Long-running backend operations that can outlive a transport connection now have
a stable identity, bounded ordered events, reconnectable snapshots, truthful
capability-backed controls, generation fencing, and a SEPARATE teardown proof. A
lost HTTP/WebSocket/process reply never makes Agent Driver blindly repeat an
unknown side effect.

### Work packages

- **A — job/event/control contracts + fencing observer.**
  `contracts/execution_job.py` (`ExecutionHandle` with start idempotency key;
  `ExecutionEvent` identity `(execution_generation, sequence)` + conflict
  detection; `ExecutionEventCursor`/`Page` gap-flagged; `ExecutionTerminalSnapshot`;
  `ExecutionControlRequest`/`Receipt` accepted-vs-applied; `TeardownReceipt`;
  reason/state enums). Optional `JobCapableBackend` protocol. `JobObserver`
  (duplicate-tolerant, generation-fenced; gap → snapshot; conflict →
  `TerminalConflictError`).
- **B — runtime observation bridge.** Tool progress (and a job's bounded observed
  events through it) reaches the runtime event log / stream as
  `RuntimeEventType.TOOL_PROGRESS`, correlated by tool_call_id — `ProgressEntry`
  gained `tool_call_id`, `ToolExecutionResult` now carries `progress_events`
  (previously dropped), and the tool stage emits one event per entry.
- **C — reconnect + fencing.** `JobSession` (idempotent + lost-start-safe start →
  `lookup` → INDETERMINATE without re-dispatch; observe-to-terminal with dedup /
  fence / gap → snapshot). `persist_job_recovery`/`restore_job_recovery` — safe,
  non-secret, JSON/checkpoint-safe (handle, cursor) for restart, fail-closed.
- **D — Stop + teardown.** `stop_job`/`JobStopOutcome` — accepted vs applied vs
  execution-terminal vs teardown-confirmed as SEPARATE, capability-backed facts.
  Teardown is destructive, runtime-owned-only, opt-in; a host-owned environment
  is never torn down; a run cancel never fabricates teardown.
- **E — timing + failure injection.** Per-phase `JobStageTiming` (start /
  first_output / observe / terminal) with typed reason codes; distinct per-job
  timeout reason codes; transport-loss resilience (observe fault → snapshot,
  never crash); indeterminate start records its reason.

## Acceptance scenarios

1 (streamed events + one terminal) · 2 (reconnect replays N+1 / compaction →
snapshot) · 3 (lost start → lookup; unresolved → indeterminate, not rerun) · 4
(duplicate terminal idempotent; conflict → protocol violation) · 5 (late/stale
generation fenced) · 6 (cooperative-only Stop claims nothing more) · 7 (hard
teardown: separate applied/execution-terminal/teardown-confirmed) · 8 (distinct
timeout reason codes + timings) · 9 (bounded + secret-rejecting event/metadata) ·
10 (restart restores handle/cursor, resolves without re-dispatch) · 11 (blocking
`run_command` still works — the adapter is unchanged). Covered by
`tests/execution/test_jobs.py`, `test_progress_bridge.py`, `test_job_session.py`,
`test_job_stop.py`, `test_job_timing.py`.

## Exact test commands + results

- `.venv/bin/python -m pytest tests/execution/` → job contracts / observer /
  session / stop / timing / progress-bridge suites pass.
- `.venv/bin/python -m pytest` (full default) → **3211 passed, 6 xfailed, 78 deselected**.
- `ruff check` clean on changed modules (pre-existing E402/F401 in
  `executor/governed.py` + `tool_stage/__init__.py` + E1135 in `search.py` are
  baseline, not introduced here). `pylint` 10/10 on `execution/jobs.py`.

## Guarantees / no-claims (precise)

- **No exactly-once without proof.** A lost start becomes INDETERMINATE and is
  never blindly re-dispatched; a duplicate terminal is idempotent; conflicting
  content is a typed protocol violation.
- **Cancellation vs termination vs teardown are distinct.** `accepted`/`applied`/
  `execution_terminal`/`teardown_confirmed` are separate; a cooperative-only
  backend claims none of the stronger facts; a terminal execution result never
  implies a released lease; a cancelled run never proves teardown.
- **Bounded + redaction-safe.** Event text is 8 KB-capped; event/metadata reject
  secret-like keys before persistence/projection.
- **No transport owned.** PTY/SSH/WebSocket are not required; `run_command`
  remains the blocking adapter.

## Known limitations / deferrals

- The `JobSession` is a host/handler-level orchestration helper; the runtime does
  not itself track active jobs (a tool handler observes its own job and reports
  progress). Wiring a specific transport is out of scope (by design).
- No concrete container/VM/fleet manager in core.

## SemVer

Additive minor `0.7.0 → 0.8.0`. `pyproject.toml`, `agent_driver.__version__`,
`uv.lock`, `CHANGELOG [0.8.0]` updated; stale `agent_driver.egg-info` removed.

## EPIC-05 predecessor gate

- Full suite green at 0.8.0; remote lifecycle proven under duplicate / loss /
  delay / restart / cancellation / stale-generation tests; Stop/teardown claims
  are precise and capability-backed.
- EPIC-05 (compatibility kit + release) builds on: the deterministic
  `FakeExecutionBackend` (now covering command/capability/lease/workspace/job
  surfaces) as the compliance simulator, the per-epic HANDOFFs as the spec, and
  the `EXECUTION_*_SCHEMA_VERSION` constants as the versioned wire contracts.
