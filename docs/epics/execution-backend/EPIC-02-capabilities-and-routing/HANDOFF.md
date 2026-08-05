# EPIC-02 Handoff — Capabilities and Safe Environment Routing

**Date:** 2026-08-05
**Status:** Delivered. Version `0.6.0`.
**Branch:** `feat/execution-backend-epic02`

## What shipped

Agent Driver now knows, in a typed and truthful way, what a host-injected
execution backend can do. It enforces those capabilities **above** the model
(withholds tools from the schema) and **below** the model (denies dispatch),
and shows the model a bounded, deterministic environment brief. The host remains
the only authority that selects the backend; capabilities are backend
observations, never model-asserted content, and `unknown` never means supported.

### Work packages

- **A — contracts + helpers.** `ExecutionCapabilitySnapshot` (typed
  `CapabilityName`→`CapabilityStatus` map, bounded `ProgramInfo` inventory,
  `environment_revision`/`lease_generation`/`digest`, secret-rejecting metadata),
  `ToolExecutionRequirement`, `RequirementCheck`, `EnvironmentBrief`,
  `EXECUTION_CAPABILITY_SCHEMA_VERSION`. Optional `CapabilityAwareBackend`
  protocol; Local/Fake/Composite report truthfully. Pure helpers
  `resolve_capability_snapshot` (fail-safe UNKNOWN), `check_requirement`,
  `derive_environment_brief`.
- **B — handshake.** `Runner._drive_steps` performs one capability handshake per
  run and installs the snapshot in a run-scoped `capability_snapshot_scope`, so
  the pre-model filter and the pre-dispatch re-check see identical truth. Missing
  `capabilities()` / a raised handshake → all-UNKNOWN.
- **C — routing.** `ToolManifest.execution_requirement` (host/registry data,
  never a model argument). Pre-model: `_request_tools_from_registry` withholds a
  tool with an unmet hard requirement. Pre-dispatch:
  `GovernedToolExecutor._capability_predispatch_block` re-checks the CURRENT
  snapshot after policy/gate/guardrail and emits a typed `capability_unmet`
  block — a model argument cannot bypass it and time-of-check/time-of-use drift
  is fenced.
- **D — environment brief.** `_inject_environment_brief` renders a deterministic,
  bounded brief and injects it as request-only context (never persisted); it
  names the capability revision.
- **E — diagnostics.** `capability_diagnostics` rides request metadata as
  `capability_audit` (backend id, revision, supported/degraded, withheld tool
  names) — no snapshot metadata values or secrets.

## Exact test commands + results

- `.venv/bin/python -m pytest tests/execution/` → capability contracts, routing,
  brief, and EPIC-01 backend tests all pass.
- `.venv/bin/python -m pytest tests/runtime/ tests/execution/ tests/tools/` → 1428 passed.
- `.venv/bin/python -m pytest` (full default) → **3111 passed, 6 xfailed, 78 deselected**.
- `ruff check` clean on changed modules (the 5 pre-existing E402/F401 in
  `executor/governed.py` are unrelated baseline lint, present on `main`).
- `pylint agent_driver/execution/capabilities.py --enable=E,W` → **10.00/10**.

Acceptance scenarios covered: 2 (installed program with no tool never callable —
by design, no auto-registration), 3 (hard-unmet/degraded/stale/unknown tool
withheld pre-model + denied pre-dispatch, typed reason), 4 (backend selection is
not a model argument), 5 (read/write route through the backend — EPIC-01
runtime-wiring tests), 6 (secret-like snapshot keys rejected), 7 (drift fenced at
dispatch), 8 (fail-safe handshake → UNKNOWN; separate handshake path), 9 (default
no-backend/no-requirement runs unchanged). 1 (verified snapshot exposes a tool +
brief names the revision) is proven by `test_capability_brief` +
`test_capability_routing`.

## Known limitations / deferrals

- **Freshness/cache TTL and lease-generation churn** are represented in the
  snapshot (`environment_revision`, `lease_generation`, `cache_key()`), but the
  run performs a single handshake — there is no periodic re-handshake or TTL
  expiry yet. Real drift across a live lease generation is EPIC-03 (leases); the
  pre-dispatch re-check already fences a snapshot that changes in scope.
- **ACP cutover** (deferred from EPIC-01) is still open; the per-capability
  fallback that `CompositeExecutionBackend.capabilities()` now expresses is the
  building block, but wiring ACP off the raw scopes remains a follow-up.
- No program is auto-registered as a tool; the inventory is descriptive only.

## SemVer

Additive minor `0.5.0 → 0.6.0`. The only removal is the 0.5.0
reserved-and-unused `CapabilitySnapshot`, superseded by
`ExecutionCapabilitySnapshot` (pre-1.0, reserved surface). `pyproject.toml`,
`agent_driver.__version__`, `uv.lock`, `CHANGELOG [0.6.0]` updated; stale
`agent_driver.egg-info` removed so installed metadata resolves to `0.6.0`.

## EPIC-03 predecessor gate

- Full suite green at 0.6.0; capability truth enforced above and below the model;
  export/schema/field snapshots match the shipped surface; backend selection
  stays host-derived and out of model tool schemas.
- EPIC-03 (execution lease + workspace) builds on: `lease_generation` on the
  snapshot + `cache_key()`, the run-scoped capability scope (extend to a
  lease-scoped snapshot with real freshness), and the deferred ACP cutover.
