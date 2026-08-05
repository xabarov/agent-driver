# Goal — EPIC-02 Capabilities and Safe Environment Routing

## Objective

Implement a revisioned execution-capability snapshot, capability-aware tool
routing, and a bounded environment brief while keeping backend selection solely
under host control.

## Mandatory context

Read the package `README.md`, `TARGET_CONTRACT.md`, `EPIC_STANDARD.md`, this
epic's `README.md`, EPIC-01 handoff, `docs/embedding.md`, tool manifest/registry
docs, request-only context docs/code, and every baseline path named here.

## Predecessor gate

- EPIC-01 public contracts, local adapter, and ACP compatibility are green.
- Public export and schema snapshots match the shipped EPIC-01 surface.
- The selected backend cannot be changed through model-visible arguments.

## Required deliverables

- Validated snapshot/status/requirement contracts.
- Backend handshake/cache with explicit freshness.
- Pre-model and pre-dispatch requirement checks.
- Bounded request-only environment brief.
- Redaction-safe diagnostics and timing.
- Deterministic drift, concurrency, timeout, and runtime acceptance tests.
- Docs, example, changelog and SemVer decision.

## Constraints

- No package installation or image management.
- No automatic conversion of installed programs into tools.
- No reuse of an existing capability type with different semantics merely to
  avoid a new contract.
- Unknown capability never means supported.

## Terminal condition

Finish only when every acceptance scenario passes and the handoff includes the
exact snapshot schema/revision policy, test evidence, limitations, and the
green predecessor gate for EPIC-03.

