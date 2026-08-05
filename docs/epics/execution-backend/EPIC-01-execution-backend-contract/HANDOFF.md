# EPIC-01 Handoff — Public ExecutionBackend Contract

**Date:** 2026-08-05
**Status:** Delivered. Version `0.5.0`.
**Branch:** `feat/execution-backend-epic01`
**Head at handoff:** `01a7e93`

## What shipped

A single supported, backend-neutral execution seam. A host injects an
`ExecutionBackend`; the built-in `bash`/`read`/`write` byte transfer flows
through it without any change to the agent loop, governance order, or the tools
themselves. The model can never select the backend.

Commits (oldest→newest):

- `e6aab35` docs — execution-backend epic package (EPIC-01..05 + standard + target contract)
- `090fc07` contracts, protocol, typed errors, facade (WP-B)
- `3974d8a` Local/Fake/Composite backends + legacy-seam adapters (WP-C)
- `8420d72` runtime wiring — `RunnerConfig.execution_backend`, `_drive_steps` scope install, executor identity enrichment (WP-D)
- `f22f6ea` per-run injection (`Agent.run(execution_backend=...)`) + recorded ACP shim decision
- `01a7e93` end-to-end acceptance, snapshots, docs, example, CHANGELOG, `0.5.0`

## Public surface (all via `agent_driver.execution`)

- Protocol: `ExecutionBackend` (runtime-checkable; `backend_id` + async
  `run_command`/`read_text`/`write_text`).
- Backends: `LocalExecutionBackend` (reference), `FakeExecutionBackend`
  (+`CommandOutcome`), `CompositeExecutionBackend`.
- Adapters: `BackendCommandRunner`, `BackendFileIO`, `identity_from_context`.
- Errors: `ExecutionError` + `UnsupportedCapabilityError`,
  `ExecutionTimeoutError`, `ExecutionTransportError`,
  `IndeterminateExecutionError`, `OutputLimitExceededError`,
  `BackendProtocolError`.
- Contracts (also in `agent_driver.contracts.execution`): `ExecutionIdentity`,
  `ExecutionBounds`, `ArtifactRef`, command/read/write request+result,
  `CapabilitySnapshot` (reserved), `EXECUTION_SCHEMA_VERSION`,
  `ExecutionTerminalState`, `CapabilityState`.

Injection: `RunnerConfig(execution_backend=...)` (process default) or
`Agent.run(..., execution_backend=...)` / `SingleAgentRunner.run(...)` (per-run,
wins over the config default). Default (no backend) = unchanged local subprocess
+ local disk.

## Exact test commands + results

- `\.venv/bin/python -m pytest tests/execution/` → **38 passed** (contracts +
  backends + runtime wiring + e2e acceptance).
- `\.venv/bin/python -m pytest tests/tools/ tests/runtime/` → 1365 passed.
- `\.venv/bin/python -m pytest tests/sdk/ tests/adapters/ tests/runtime/ tests/contracts/` → 1297 passed.
- `\.venv/bin/python -m pytest` (full default) → **3083 passed, 6 xfailed, 78 deselected**.
- `\.venv/bin/python -m ruff check <changed modules>` → clean; `ruff format` applied.
- `\.venv/bin/python -m pylint agent_driver/execution/ agent_driver/contracts/execution.py --enable=E,W` → **10.00/10**.

Acceptance scenarios 1, 2, 3, 4, 6, 7 pass through the real runner + governed
executor (`tests/execution/test_e2e_acceptance.py`). Scenario 5 (read/write
routing) is proven at the real routed-helper seam the tools use
(`tests/execution/test_runtime_wiring.py`). Scenario 9 (public imports/schemas/
JSON round-trips snapshot-tested) is `tests/execution/test_execution_contracts.py`.

## Known limitations / deliberate deferrals

- **Scenario 8 / ACP cutover — deferred to EPIC-02.** ACP keeps its current
  terminal/file routing via the public `command_runner_scope`/`fs_io_scope`
  (which the new adapters themselves use), so it stays green with no divergence.
  A faithful single-backend cutover needs (a) per-capability local fallback in
  `CompositeExecutionBackend` (ACP's terminal and fs are *independently* absent,
  each with a local fallback today) and (b) `execution_backend` threaded through
  `Agent.resume()` too (tools run in the resume leg). Both belong with the
  EPIC-02 capability model. `CompositeExecutionBackend` ships now as the
  supported shim primitive. Rationale in `TARGET_CONTRACT.md` → "Revision
  (2026-08-05)".
- **Per-run selection is a HOST concern** (one prepared env per session), not
  model-driven lease selection — that stays EPIC-03.
- `CapabilitySnapshot` and the `CapabilityState`/`DEGRADED` vocabulary are
  defined but not yet produced by any method (reserved for EPIC-02).
- `LocalExecutionBackend.read_text` enforces `max_bytes`; when reached through
  the `BackendFileIO` adapter the tool's own post-read size guard is the active
  bound (the adapter passes an inert large bound), preserving exact tool
  behavior.

## SemVer

Additive public surface ⇒ minor bump `0.4.0 → 0.5.0` (`pyproject.toml`,
`agent_driver.__version__`, `CHANGELOG [0.5.0]`). No breaking change; default
behavior unchanged. Note: a stale `agent_driver.egg-info` (0.4.0) in the tree was
removed so installed metadata resolves to `0.5.0`; a clean install regenerates it.

## EPIC-02 predecessor gate (green)

- Branch clean at `01a7e93`; full suite green (3083 passed).
- Baseline for EPIC-02: the reserved `CapabilitySnapshot`/`CapabilityState`
  contracts, the `CompositeExecutionBackend` shim, and the per-capability
  fallback + `resume()`-leg threading noted above are the concrete first tasks
  for the ACP cutover under a capability model.
