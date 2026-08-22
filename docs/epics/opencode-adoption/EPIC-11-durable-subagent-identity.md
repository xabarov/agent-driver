# EPIC-11 — Durable, resumable subagent identity (L)

Status: **Stage 1 (durable addressable identity) + the Stage-2 Postgres substrate DONE
(2026-08-22); resume execution + promote/extend deferred.** Track:
[opencode-adoption](README.md). Source idea:
opencode's single biggest structural advantage — a subagent **is** a durable, resumable
session (`task_id` resumes it) + fg→bg `promote` / running-child `extend`.

## The landscape

We had two subagent stacks (see [[coordination-theme]]): the SDK in-proc/ephemeral path and
the runtime **persisted stack-B** (`subagents/executor.py` + `SubagentStore`). Stack-B
already persists every spawned child as a `SubagentRun` (SQLite/PG-capable) carrying
`subagent_run_id`, `parent_run_id`/`parent_attempt_id`/`parent_checkpoint_id`, **`child_run_id`**
(set to the child agent's real `output.run_id`), `task_id`, `status`, `result`, and
`terminal_state`. So the *record* of a durable child already existed — but it was only
addressable **by parent** (`list_runs(parent_run_id)`); there was no way to look up a
specific child by its own `child_run_id`, which is the primitive a resume flow needs.

## Stage 1: addressable durable identity (this slice)

- `SubagentStore.find_run_by_child_run_id(child_run_id) -> SubagentRun | None` — added to the
  Protocol and both backends:
  - `InMemorySubagentStore`: scans the parent-keyed rows.
  - `SqliteSubagentStore`: SQLite JSON1 `json_extract(payload, '$.child_run_id')` (with a
    full-scan fallback when JSON1 is absent) — so a child is resolvable **across process
    restarts** without knowing its parent.
- `Agent.find_subagent_run(child_run_id)` — the SDK read accessor over the configured
  `subagent_store`, returning the persisted run's status/result/parent linkage (or `None`).

This turns the already-persisted child record into an **addressable durable identity** — the
foundation Stage 2 resumes from. Tests: `tests/subagents/test_durable_child_identity.py`
(lookup across parents on both backends; missing/empty; survives a SQLite reopen; the Agent
accessor). Full `tests/subagents` + SDK + export/layering sweeps green.

## Stage 2 (part): PG-backed `SubagentStore` (DONE)

`PostgresSubagentStore` (`agent_driver/subagents/postgres_store.py`) puts durable subagent
run/group state on the **same Postgres control plane** as the approval / abort / plan-artifact
stores (it reuses `_PostgresControlStoreBase` from `runtime/control/postgres.py`) — the
substrate the epic targets for unifying the fragmented subagent stacks, instead of the
per-process SQLite backend. It implements the full `SubagentStore` protocol, including
Stage-1's `find_run_by_child_run_id` (an indexed `child_run_id` column + `WHERE child_run_id
= …`, resolving across process restarts) and the idempotency-key row reuse. `psycopg` (v3) is
imported lazily via the shared base, so importing the module is free without the
`agent-driver[postgres]` extra. Drop-in for `RunnerConfig.subagent_store`. **Live-verified
against `postgres:16`** (runs/find/idempotency/groups/reopen); the opt-in conformance test
(`tests/subagents/test_postgres_subagent_store.py`, gated on `AGENT_DRIVER_RUN_POSTGRES_TESTS`)
plus an offline protocol-surface test cover it.

## Deferred (the rest of the L)

- **Stage 2 — `resume_subagent(child_run_id, prompt)`.** Re-drive a persisted child from its
  last checkpoint under the same `child_run_id`. Requires: the child run to be journaled/
  checkpointed under a **stable, caller-addressable** `child_run_id` (today it is generated
  per spawn and the child runner's checkpoint store is not keyed for external resume), and a
  runner entry point that loads the child's checkpoint and continues with a new prompt. This
  is the large, risky part — it touches the child execution path and the checkpoint contract
  — and is staged separately.
- **PG-backed `SubagentStore`.** Stack-B's durable backend is SQLite today; the epic's
  "PG control-plane" wiring (a `PostgresSubagentStore` alongside the existing PG approval/
  abort/plan stores) is a follow-on so a child is durable on the same substrate as the rest
  of the control plane.
- **fg→bg `promote` + running-child `extend`** (opencode `background/job.ts`) — follow-ons
  once resume exists: promote a foreground child to a background job mid-flight, or extend a
  running child's budget.

## Why staged

Stage 1 is additive and non-invasive (a read primitive + an SDK accessor) and unblocks
inspection/addressing of durable children now. Stage 2 changes the child execution +
checkpoint contract and is where the real risk lives; landing the identity primitive first
keeps that change reviewable on a stable foundation.
