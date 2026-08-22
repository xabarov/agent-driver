# EPIC-01 — Import-layering guard (S)

Status: **DONE (2026-08-22)** — `tests/contracts/test_import_layering.py` green
(contracts-purity A + embedding-facade B + core-impl→sdk C, modulo one baselined
exception); negative-smoke verified (injecting `contracts → runtime` fails the guard).
Track: [opencode-adoption](README.md). Source idea:
opencode enforces its dependency direction (`Schema → Core/Protocol → Server`; `Client`
never touches `Core/Server`) *physically* via package boundaries + a `git diff` CI gate
(`AGENTS.md`, `packages/schema/AGENTS.md`). Python has no package-boundary enforcement,
so we do it with a test.

## Why

`docs/embedding.md` already documents a dependency direction (wire `contracts` at the
bottom; per-concern facades; the `agent_driver.embedding` aggregate), and memory
`sdk-theme` records the recurring failure mode: **consumers (excel-ai) reaching into
deep internals** like `runtime.single_agent.lifecycle.config_sections`. Nothing today
*mechanically* prevents the layering from eroding — a stray `from agent_driver.runtime…`
in `contracts`, or an impl module importing the `sdk` facade, passes silently. This epic
adds a cheap AST test that fails on such drift. Pure win, no runtime change, no boundary
risk.

## The contracts (verified against current code, all module-level imports)

- **A — `agent_driver.contracts.*` is a pure wire/data leaf.** It must import NO
  implementation package (the set below). It may import other `agent_driver.contracts.*`
  submodules and any third-party/stdlib. *Verified: holds today (0 violations)* — this is
  the crown-jewel invariant (opencode's schema-purity).
- **B — `agent_driver.embedding` imports only the public facades.** Allowed roots:
  `agent_driver.{contracts, sdk, runtime, llm, tools, execution, memory}` (+ itself). It
  must not import `runtime.single_agent.*` deep internals or underscore-private modules —
  it is identity re-exports of the public surface. *Verified: holds today.*
- **C — no CORE-implementation package imports the top facade `agent_driver.sdk`.**
  Core-impl packages (`runtime, tools, llm, execution, memory, context, permissions,
  harness, observability, security, structured, subagents, fs, persistence, scheduler,
  batch, prompts, code_agent, skills`) sit BELOW the `sdk` facade; importing it is a
  layering inversion. *One pre-existing exception* (see Baseline). **App/adapter/peer
  packages that sit ABOVE `sdk` and legitimately compose it are excluded from C:**
  `cli, server, adapters, mcp_server, gateway, evals, agents` (a CLI/HTTP-server/protocol
  adapter naturally imports the facade).

The **implementation-package set** (what `contracts` may not import — the full set,
since `contracts` is below everything): `runtime, tools, llm, execution, memory, harness,
cli, adapters, mcp_server, sdk, gateway, code_agent, batch, scheduler, observability,
security, skills, context, prompts, permissions, persistence, structured, subagents, fs,
agents, server, evals`. Contract C governs only the subset strictly below `sdk`
(full set − app packages − `sdk`).

Deliberately NOT enforced: `sdk`/facades importing `runtime.single_agent.*`. The `sdk`
facade legitimately composes the runner (`sdk/factory.py` imports `RunnerConfig`,
`completion`) — that is the intended top-down edge, not a leak. (The reach-in problem is
on the CONSUMER side, which this repo's test cannot see; excel-ai should run its own
guard forbidding `agent_driver.runtime.single_agent.*` — noted as a follow-on for the
consumer.)

## Baseline (pre-existing exceptions, each a TODO)

- `agent_driver.llm.error_classifier` → `agent_driver.sdk.errors` (Contract C). The
  provider-error classes (`ProviderError`/`ProviderStatusError`/…) live under `sdk.errors`
  but are consumed by the `llm` layer — a misplacement. Allowlisted with a TODO to relocate
  them to `contracts` (or `llm`) and re-export from `sdk.errors` in a follow-up. New C
  violations still fail.

The baseline is an explicit dict in the test; shrinking it is the follow-up work, growing
it requires a deliberate edit + justification (like a lint baseline / import-linter
`ignore_imports`).

## Implementation

`tests/contracts/test_import_layering.py`:
- Walk each guarded package's `.py` files with `ast`; collect **module-level** (top-of-file,
  not under `if TYPE_CHECKING:`, not function-local) `Import`/`ImportFrom` targets that
  start with `agent_driver.`.
- For each edge, check it against A/B/C; collect violations minus the baseline.
- Assert the violation set is empty, printing each `file → forbidden import (contract)`.

Module-level only, because that is the edge that exists at import time and defines the
layer graph; `TYPE_CHECKING` and lazy function-local imports are documented deliberate
escape hatches (a lazy import to break a cycle is intentional, not drift).

## Test plan / acceptance

- The test is GREEN on current `main` (A and B hold; C holds modulo the one baseline entry).
- Introduce a temporary `from agent_driver.runtime import RunnerConfig` into a `contracts`
  module locally → the test FAILS naming it (manual smoke, reverted).
- Full suite stays green (test-only addition).

## SemVer / boundary

Patch — test-only, no public surface or behaviour change. Domain-neutral. No CHANGELOG
entry required (internal quality gate), but note it in the epic README.

## Follow-ups (out of this epic)

- Shrink the baseline: relocate `sdk.errors` provider errors to `contracts`/`llm`.
- A consumer-side guard in excel-ai forbidding `agent_driver.runtime.single_agent.*`
  imports (the actual reach-in site) — the natural completion of this discipline.
