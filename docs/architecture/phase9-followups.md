# Phase 9 Follow-ups: Crash Safety and Cleanup

This follow-up plan narrows the next Phase 9 work to the roadmap exit gaps that
remain highest risk for production orchestration:

- retry after parent crash must not duplicate children;
- failed/timed-out children must not leave stale `running` rows;
- child handoff/merge must remain bounded and provenance-preserving.

## Scope

The current in-memory implementation already supports idempotent spawn keys and
basic group/run persistence, but it does not yet have an explicit reconciliation
pass for stale rows or a recovery API that can be called on parent restart.

## PR 1: Recovery Snapshot and Reconcile API

Goal: make restart recovery explicit in the store contract.

Changes:

- Extend `InMemorySubagentStore` with:
  - `list_running_runs(parent_run_id)` helper;
  - `mark_stale_running(parent_run_id, reason)` to transition orphaned
    `running` rows into deterministic terminal state;
  - `resume_group(parent_run_id, group_id)` to return existing child rows and
    preserve idempotency map decisions.
- Add bounded metadata fields for reconciliation provenance:
  - `recovery_reason`;
  - `recovered_at`;
  - `recovery_source`.

Acceptance:

- repeated recovery calls are idempotent;
- stale rows are transitioned once and never re-opened as `running`.

## PR 2: Parent Crash-Retry Guard in Executor

Goal: prevent duplicate child spawn on retry.

Changes:

- Update `execute_subagent_group_sync(...)` to detect pre-existing child rows
  for the same `(parent_run_id, group_id, task_id|idempotency_key)` before
  creating new pending rows.
- Reuse existing rows for already-completed children and only spawn missing
  tasks.
- Persist reconciliation outcome in group metadata (`reused_child_runs`,
  `spawned_child_runs`).

Acceptance:

- retrying same group after simulated crash reuses child rows;
- no duplicate `subagent_run_id` rows are created for reused tasks.

## PR 3: Bounded Provenance Assertions

Goal: guarantee merged child data stays inspectable and size-limited.

Changes:

- Add guard in merge path to cap carried metadata size and summary length.
- Ensure provenance retains child run id and merge strategy for every carried
  contribution.
- Add explicit tests for bounded handoff + bounded merged summary under
  multi-child partial failure.

Acceptance:

- merged outputs remain under configured bounds;
- replay metadata still identifies which child produced each merged fact.

## Required Verification

For each PR:

```bash
python -m pytest tests/subagents -q
python -m pytest tests/runtime/test_subagent_integration.py -q
python -m pytest tests/evals/test_subagent_*.py -q
```

For live lane when credentials are present:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 python -m pytest tests/runtime/test_live_subagent_openrouter.py -q
```
