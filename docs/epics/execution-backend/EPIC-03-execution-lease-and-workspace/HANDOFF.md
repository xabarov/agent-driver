# EPIC-03 Handoff — Execution Lease and Workspace Lifecycle

**Date:** 2026-08-05
**Status:** Delivered. Version `0.7.0`.
**Branch:** `feat/execution-backend-epic03`

## What shipped

One task-scoped, generation-bound execution lease that a host-injected backend
grants, Agent Driver acquires/attaches once and reuses across the whole agent
loop, every built-in filesystem operation routes to with backend-relative path
safety, and that is released (runtime-owned) or detached (host-owned) on every
exit. The backend owns infrastructure; Agent Driver owns correct use of the
lease inside the run.

### Work packages

- **A — contracts + manager.** `contracts/execution_lease.py`
  (`ExecutionLeaseRequest`/`ExecutionLeaseRef`/`ExecutionLease`/`LeaseReceipt`/
  `WorkspacePaths`, ownership/state/phase enums). Optional `LeaseCapableBackend`.
  `ExecutionLeaseManager`: idempotent acquire/attach/reuse; release/detach once;
  fail-closed (`LeaseNotUsableError`/`UnsupportedCapabilityError`), never a silent
  local fallback. `ExecutionLeaseRef.fences()` a stale generation.
- **B — run/checkpoint integration.** `RunContext.execution_lease_manager` (live);
  `RunnerConfig.execution_lease_ownership` (or host attach via
  `app_metadata["execution_lease_ref"]`). Acquire/attach in `_drive_steps`,
  persist the safe ref for resume, release in the authoritative outer `finally`.
  Fail-closed produces a terminal FAILED output, never local fallback.
- **C — complete workspace routing.** `contracts/execution_workspace.py` +
  `WorkspaceCapableBackend` (list/glob/grep/stat/delete). `execution/pathsafety.py`
  `validate_workspace_path` — lexical, no-disk, rejects traversal/escape, enforces
  writable roots. Routing-aware `_paths.py` resolvers + routed handlers for
  read/write/edit/patch/glob/grep/artifact_*/notebook_edit (no local stat, no
  local fallback when a backend is active). Filesystem builtins declare
  FILE_READ/FILE_WRITE requirements.
- **D — artifact bridge.** `execution/artifacts.py` — bounded model-facing
  reference (`execution_artifact_reference_payload`) + context mapping
  (`execution_artifact_to_context_ref`, digest preserved). `BackendCommandRunner`
  propagates `ExecutionCommandResult.artifact`; `_bash_handler` surfaces a bounded
  reference (never full content).
- **E — cleanup + ownership.** Idempotent release on all terminal/exception/
  timeout/cancellation exits; PAUSED retains the lease (resume re-attaches);
  per-phase timings surfaced to `metadata["execution_lease_receipts"]`; subagent
  default policy **ISOLATE** (a child never acquires or releases the parent lease).

## Acceptance scenarios

1 (acquire once, reuse) · 2 (no accidental sharing) · 3 (host-owned attach/detach)
· 4 (exactly one release across normal/exception/timeout/cancel) · 5 (resume
attaches by safe ref; stale generation fails closed) · 6 (remote read/write/edit/
delete/list/glob/grep never touch local disk) · 7 (missing file capability →
withheld/denied, no local fallback) · 8 (traversal/escape rejected as bounded
`WorkspacePathError`) · 9 (large output → bounded digest+size reference) · 10
(lease phase timings independently observable) · 11 (subagent ISOLATE policy).
All covered by `tests/execution/test_lease*.py`, `test_workspace_ops.py`,
`test_routed_*.py`, `test_artifact_bridge.py`, `test_filesystem_capability_gating.py`.

## Exact test commands + results

- `.venv/bin/python -m pytest tests/execution/` → lease/workspace/routing/bridge/
  cleanup suites pass.
- `.venv/bin/python -m pytest` (full default) → **3171 passed, 6 xfailed, 78 deselected**.
- `ruff check` clean on changed modules (pre-existing E402/F401 in
  `executor/governed.py` + E1135 in `filesystem/search.py:parse_grep_args` are
  baseline, not introduced here). `pylint` 10/10 on the new execution modules.

## Known limitations / deferrals

- **Streaming/reconnect/hard-teardown** command semantics are EPIC-04 (the lease
  reserves the vocabulary; `capabilities()` reports them UNKNOWN/UNSUPPORTED).
- **Lease freshness/TTL churn** across a live generation is represented
  (`ttl_seconds`, `expires_at`, `cache_key()`) but not actively expired mid-run;
  a resumed run re-attaches and fails closed on a stale generation.
- **Subagent lease sharing/inherit** beyond the ISOLATE default is intentionally
  not implemented; it needs an explicit policy contract (future work).
- **ACP cutover** (deferred from EPIC-01) remains open.
- No concrete container/VM/fleet manager lives in core (by design).

## SemVer

Additive minor `0.6.0 → 0.7.0`. `pyproject.toml`, `agent_driver.__version__`,
`uv.lock`, `CHANGELOG [0.7.0]` updated; stale `agent_driver.egg-info` removed so
installed metadata resolves to `0.7.0`.

## EPIC-04 predecessor gate

- Full suite green at 0.7.0; one lease spans the loop and all claimed workspace
  tools; ownership/resume/expiry/artifacts/path-safety/cleanup proven; backend
  selection stays host-derived and out of model tool schemas.
- EPIC-04 (execution jobs, events, control, hard teardown) builds on: the lease
  `LeaseLifecyclePhase`/`LeaseReceipt` timing surface, the reserved
  event/control/reconnect/teardown capability vocabulary, and
  `ExecutionCommandResult`/`ArtifactRef` as the per-execution result shape.
