# EPIC-05 — Backend Compatibility Kit and Release Surface

Status: blocked on EPIC-04.

## Outcome

Backend authors can implement the public protocol without reading Agent Driver
internals and can run a deterministic compliance suite that reports exactly
which capabilities and guarantees their adapter has proved. Agent Driver ships
a supported, documented release surface with a migration path from the legacy
execution hooks.

## Why this epic exists

Protocol types alone do not make backends interoperable. The difficult bugs
appear around governance ordering, duplicate dispatch, output bounds, path
routing, reconnect, cancellation, and cleanup. Those behaviors need executable
qualification and an evidence report, not self-declared compatibility.

## Baseline to inspect

- all earlier epic implementations and handoffs
- existing harness capability packs and compatibility report patterns
- deterministic fake providers/backends and failure injection utilities
- public facade/export and schema snapshot tests
- package examples, docs, changelog, and release/version tooling
- ACP and local compatibility suites

## In scope

1. Publish a backend-author guide using only supported Agent Driver imports.
2. Promote the deterministic fake backend into a configurable simulator for
   success, delay, duplicate, gap, stale generation, transport loss,
   indeterminate dispatch, cancellation, artifact, and teardown behavior.
3. Add a reusable compatibility runner/suite that can be pointed at any backend
   implementation without requiring a model or external infrastructure.
4. Produce a machine-readable, redaction-safe report containing contract
   version, backend/environment revision, scenarios, evidence digests, proved
   capability statuses, failures, skipped checks, and no-claims.
5. Add an optional real-run layer using `FakeProvider` and the actual runner,
   governed executor, built-in tools, events, checkpoints, and recovery path.
6. Qualify the built-in local compatibility backend. Qualify ACP where its
   semantics overlap, without pretending it provides unrelated lease/teardown
   guarantees.
7. Provide migration/deprecation guidance for `AsyncCommandRunner`,
   `AsyncFileIO`, and direct local callbacks. Keep the documented deprecation
   window required by the repository's public API policy.
8. Finalize public exports, examples, reference docs, changelog, versioning, and
   release notes.

## Non-goals

- Certifying the security of an external container or worker implementation.
- Testing operating-system hardening, network policy, tenant isolation, image
  freshness, or secret brokers inside Agent Driver's deterministic suite.
- Shipping a production fleet backend in core.
- Marking a skipped or unsupported scenario as passed.
- Requiring live model/provider calls for protocol compliance.

## Required compliance groups

| Group | Minimum proof |
| --- | --- |
| Contract | validation, round trip, version negotiation, unsupported errors |
| Governance | deny/approval before dispatch, immutable host routing |
| Identity | run/attempt/tool/request/lease/execution propagation and fencing |
| Lease | acquire/attach/reuse/expiry/ownership/detach/release idempotency |
| Workspace | full claimed file routing, path escape rejection, no local fallback |
| Output | bounds, truncation, artifacts, digest/size, redaction |
| Events | order, duplicate delivery, cursor replay, gaps, snapshot recovery |
| Dispatch | lost reply lookup, indeterminate result, no unsafe redispatch |
| Control | supported/no-claim behavior, accepted/applied distinction |
| Recovery | runtime restart, stale generations, late result quarantine |
| Teardown | terminal versus release, confirmed/failed/unknown cleanup |
| Concurrency | context isolation across runs and leases |
| Timing | queue/acquire/ready/start/first-output/terminal/teardown separation |

## Design constraints

- Reports distinguish `passed`, `failed`, `unsupported`, `skipped`, `stale`,
  and `no_claim`.
- A backend advertises only the capabilities proved for its exact contract and
  environment revision.
- Deterministic tests do not need Docker, network, credentials, or a live LLM.
- Infrastructure/security certification belongs to the external backend's own
  test layer and may be linked as separate evidence.
- Examples import only supported facades; internal imports fail a test or doc
  check.
- Compatibility artifacts contain no raw secrets or unbounded output.

## Work packages

### A. Backend author kit

Write the minimal implementation recipe, lifecycle diagrams, error mapping,
thread/async expectations, redaction rules, and a small adapter example.

### B. Deterministic simulator

Build reusable scripts/fixtures for every required failure mode. Use virtual or
controlled time where possible to keep CI fast and deterministic.

### C. Compliance runner and report

Run capability-selected scenarios, emit machine-readable and concise Markdown
reports, hash evidence, and fail when a claimed mandatory capability is not
proved.

### D. Built-in qualification

Run the suite against local compatibility and the supported ACP subset. Record
unsupported/no-claim rows rather than adding fake behavior.

### E. Migration and release

Publish compatibility/deprecation guidance, runnable examples, public API
inventory, changelog and correct pre-1.0 version update.

## Acceptance scenarios

1. A minimal third-party-style fake backend can implement the protocol using
   only public imports and pass its claimed subset.
2. A backend that claims hard teardown but only acknowledges cancellation fails
   the teardown group and reports the precise missing proof.
3. A backend that leaks to local filesystem during remote workspace operations
   fails the workspace group.
4. Duplicate events, lost dispatch replies, stale generations, output overflow,
   secret-like payloads, and teardown failures are each detected deterministically.
5. Skipped optional capabilities remain skipped/unsupported and never inflate
   the compatibility result.
6. The local backend passes its full declared profile through direct contract
   tests and the real runner/governed-tool path.
7. ACP passes only its applicable declared profile and keeps existing behavior.
8. Reports are deterministic apart from documented timestamps/IDs, contain
   digests and version/revision, and are redaction-safe.
9. Migration examples work for legacy command/file hook users through the
   documented deprecation window.
10. A clean installation can run the no-live compatibility suite without
    external infrastructure or credentials.

## Definition of done

- A backend author can implement and qualify an adapter from public docs alone.
- Claimed guarantees are backed by deterministic scenario evidence.
- Local and ACP profiles are published truthfully.
- Migration, exports, schemas, changelog, version, examples, full tests, and
  quality checks are coherent.
- Remaining infrastructure concerns are explicitly routed outside Agent Driver.

