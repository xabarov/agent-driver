# EPIC-05 Handoff — Backend Compatibility Kit and Release Surface

**Date:** 2026-08-05
**Status:** Delivered. Version `0.9.0`. **Final epic — the execution-backend package is complete.**
**Branch:** `feat/execution-backend-epic05`

## What shipped

A backend author can implement the public `ExecutionBackend` protocol and prove
exactly which capabilities and guarantees their adapter provides — from public
docs alone, with no live LLM, Docker, network, or credentials — and Agent Driver
ships a documented release surface with a legacy migration path.

### Work packages

- **A — backend author kit.** `examples/cookbook/21_backend_compliance.py`: a
  minimal third-party-style backend built with ONLY public
  `agent_driver.execution` imports, qualified by the suite.
  `docs/execution-backend-migration.md` covers surfaces, error mapping,
  redaction, and the legacy-hook migration.
- **B — deterministic simulator.** `FakeExecutionBackend` already covers success/
  delay/duplicate/gap/stale-generation/transport-loss/indeterminate/cancellation/
  artifact/teardown via its scripting knobs (`job_pages`, `lose_start`,
  `job_terminal`, `control_receipt`, `teardown_confirmed`, `acquire_state`, …).
- **C — compliance runner + report.** `contracts/execution_compliance.py`
  (`ComplianceReport`/`ComplianceCheck`/`ComplianceStatus`/`ComplianceGroup`,
  versioned + bounded + redaction-safe) and `execution/compliance.py`
  (`run_compliance` + `render_markdown`). Groups run only when advertised; an
  unadvertised group is `no_claim`; an advertised-but-unproved guarantee is
  `failed`; a truthful unconfirmed teardown is `unsupported`.
- **D — built-in qualification.** The suite qualifies `FakeExecutionBackend`
  (full profile) and `LocalExecutionBackend` (command + identity proved; remote-
  lifecycle groups `no_claim`) via direct contract tests.
- **E — migration & release.** Migration/deprecation guide, docs, changelog,
  version bump, and this handoff.

## Acceptance scenarios

1 (third-party fake passes its claimed subset via public imports) · 2 (claims
hard teardown but only acknowledges → teardown group FAILS with the precise
missing proof) · 4 (duplicate / lost dispatch / stale generation / output
overflow / secret-like payloads / teardown failure each detected
deterministically — via the EPIC-01..04 suites + the compliance groups) · 5
(skipped/unclaimed capabilities stay `no_claim`/`unsupported`, never inflate the
result) · 6 (local backend qualifies its declared profile) · 8 (reports are
versioned, digest-bearing, redaction-safe, JSON round-trip) · 10 (clean-install,
public-import-only suite runs with no external infra). Covered by
`tests/execution/test_compliance.py`. ACP (scenario 7) keeps its existing
behavior and is not falsely advertised lease/teardown guarantees (ACP cutover to
the seam remains deferred — see EPIC-01/03 handoffs).

## Exact test commands + results

- `.venv/bin/python -m pytest tests/execution/test_compliance.py` → 8 passed.
- `.venv/bin/python -m pytest` (full default) → **3220 passed, 6 xfailed, 78 deselected**.
- `.venv/bin/python examples/cookbook/21_backend_compliance.py` → the author
  backend reports OK (3 passed, unclaimed groups `no_claim`).
- `ruff`/`pylint` clean on the new modules (10/10). Pre-existing baseline lint in
  `executor/governed.py`, `tool_stage/__init__.py`, `search.py` is untouched.

## Known limitations / no-claims

- The compliance suite proves protocol conformance + runtime guarantees
  (governance ordering, identity/fencing, bounds/redaction, reconnect, cleanup),
  NOT infrastructure/security of an external container/VM/tenant/network/secret
  broker — that belongs to the backend's own test layer, linked as separate
  evidence.
- No production fleet backend ships in core (by design).
- The ACP cutover to the execution seam is still deferred (EPIC-02 follow-up); the
  compliance kit does not advertise unproved ACP lease/teardown guarantees.

## SemVer

Additive minor `0.8.0 → 0.9.0` (pre-1.0, per the package release discipline).
`pyproject.toml`, `agent_driver.__version__`, `uv.lock`, `CHANGELOG [0.9.0]`
updated; stale `agent_driver.egg-info` removed.

## Package status

The execution-backend package (EPIC-01 contract → EPIC-02 capabilities → EPIC-03
lease/workspace → EPIC-04 jobs/events/control → EPIC-05 compatibility kit) is
**complete**: 0.5.0 → 0.9.0, one deterministic public surface, a compliance suite
that qualifies any backend, and a legacy migration path. Remaining threads are
external (concrete backend infrastructure) or explicitly deferred (ACP cutover).
