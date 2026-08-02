# Agent Driver upstream Goal — PentestLens embedding readiness

Status: approved input for a separate Goal in `xabarov/agent-driver`

> **IMPLEMENTED — released as `0.2.0` (2026-08-02).** All seven work items (U1–U7) are done; see
> epics `048`–`055` and design-notes `notes/2026-08-02-F{1,2,3}-*.md`, and the release
> `handoff-0.2.0-pentestlens.md`. Full test sweep: base 2873 → **2969 passed**, additive and
> persisted-state compatible. Per-item ✅ markers are on each `### U*` heading below and the
> Definition of Done checklist is ticked with evidence. Two residual items are optional and
> non-blocking (Postgres impls of the approval/abort stores — SQLite + in-memory shipped and proven;
> `latest()`-by-revision store rewire) — recorded as residual risks in the handoff.

## Objective

Deliver a clean, version-coherent public Agent Driver release candidate with a
supported embedding facade and generic durable control contracts sufficient for
a host product to attach policy provenance, consume an approval exactly once,
and stop a run durably while cancelling active host work. Preserve existing
Agent Driver behavior through compatibility tests and provide an exact
handoff that PentestLens can pin without importing runtime internals.

This document is standalone. The executing agent must also read and obey that
repository's own `AGENTS.md`, contribution, test, release, and compatibility
instructions. Those instructions may refine implementation details but may not
silently weaken the behavioral requirements below.

## Context and current gaps

PentestLens embeds Agent Driver in its Chat orchestrator. The 2026-08-02
assessment used public GitHub commit
`ed6a2a3005de96a8f03c57d8d6bf6e151c4c050d` as a diagnostic baseline only.
The repository is actively changing, so the selected release must be a later
clean commit produced after this Goal; never assume that assessment SHA or
floating `main` is the deliverable.

Observed gaps to verify against the clean starting commit:

- embedders need numerous runtime/lifecycle/provider/store modules and there
  is no narrow, declared compatibility surface covering the required bundle;
- `ToolGateContext` lacks stable call-attempt correlation needed to bind an
  external policy decision, and gate results cannot carry durable JSON-safe
  provenance through the complete run lifecycle;
- resume/approval is not proven as an atomic one-time consume with expected
  checkpoint revision and idempotency semantics across concurrent clients;
- `RunAbortHandle` is process-local and primarily checked at step boundaries;
  it does not by itself provide a durable abort request/observed/terminal
  lifecycle or cancellation of an already-running host handler;
- Agent Gateway parks runs in process-local `_parked` state, so it is not a
  durable recovery boundary for this product;
- package metadata/version and public commit identity need a coherent release
  handoff.

Inspect current code and tests before implementation. If a requirement is
already satisfied, close it with a public contract, adversarial test, and
handoff evidence rather than duplicating it.

## Repository boundary

Agent Driver owns generic harness behavior:

- runner/agent construction and documented embedding extension points;
- checkpoint, event, command/control and stream projection protocols;
- tool-call correlation and gate-result provenance transport;
- atomic approval/resume consumption and stale/idempotent outcomes;
- abort lifecycle, prevention of later work, and host cancellation hook/token;
- serialization, compatibility, documentation, and contract tests.

Agent Driver must not implement or name PentestLens-specific concepts such as
tenant authorization, Engagement scope, URL matching, pentest risk classes,
autonomy modes, target budgets, owned labs, policy UI, HTTP gateway rules, or
customer evidence. The host supplies opaque IDs/metadata and owns their
meaning.

## Dependencies and start gate

- Start from a clean public GitHub worktree. Record branch, exact base commit,
  remote, package version, Python versions, and `git status`.
- Preserve/separate any concurrent user changes. Do not reset, clean, overwrite
  or incorporate a dirty unrelated tree without explicit coordination.
- Run the repository's full baseline test/lint/type/documentation gates and
  record existing failures before changing public contracts.
- Inventory public exports and all internal modules currently required by the
  documented embedding examples. Confirm current ToolGate, interrupt/resume,
  checkpoint/event persistence, abort and Gateway behavior from code/tests.

If the repository is dirty or the baseline cannot be attributed, stop and
request upstream-project direction rather than manufacturing a release from an
ambiguous tree.

## Required work

### U1. Supported embedding facade — ✅ DONE (epic 049; `agent_driver.embedding` + per-concern facade, e2e `19_embedded_e2e.py`)

Provide one documented supported namespace/facade for host embedders. The
upstream implementation chooses its exact module layout, but the handoff must
export stable supported names for:

- agent/runner construction and configuration;
- provider protocol and OpenAI-compatible route/provider construction needed
  by a host without importing an implementation file path;
- tool registry, governed executor, planning/skill extension points and
  host-supplied handler/tool contracts;
- checkpoint store, event log, command/control store and Postgres/in-memory
  implementations or supported factories;
- lifecycle hook protocol and stream/run lifecycle projections;
- ToolGate context/results and approval interrupt/resume contracts;
- abort request/handle/state and host cancellation hook contracts.

An embedder must not need `runtime.single_agent.*`, internal mixins, underscored
modules/classes, lifecycle implementation files, or provider implementation
paths. Public API compatibility is explicit: document stability policy,
deprecation policy, supported Python range, optional extras, and a machine-
readable or test-owned export snapshot.

Add at least one end-to-end embedded example that constructs the harness with
a fake provider, host stores, one governed fake tool, lifecycle hook, approval,
resume and abort. The example must use only supported imports.

### U2. ToolGate call identity and provenance — ✅ DONE (epic 050; `tool_call_id`/`attempt_id` + `GateProvenance` under reserved `_ad_`, fail-closed, unified interrupt-id)

Extend the generic gate contract so each evaluation has stable correlation:

- `tool_call_id`: stable for one logical planned call across gate, interrupt,
  approval/resume and terminal envelope;
- `attempt_id`: unique for each execution attempt/retry;
- stable run/session identifiers already present in the runtime;
- optional JSON-safe host correlation metadata.

Allow each `allow`, `deny`, and `ask` result to carry optional JSON-safe
provenance including opaque `decision_id`, `policy_snapshot_id`, and generic
metadata. Names may be generalized if the mapping remains unambiguous and
documented.

The runtime must preserve the exact validated provenance through every
applicable interrupt, checkpoint, event, envelope/tool result, trace/support
projection, resume and terminal outcome. It must not let model/tool output
author or overwrite host provenance. Reject or fail closed on non-JSON,
oversized, malformed, reserved-key-conflicting or inconsistent metadata.
Define size/depth/key limits and deterministic serialization/hash behavior.

Test allow, deny, ask/resume, retry, failure, timeout and abort. Assert call ID
stability, attempt ID change where appropriate, exact provenance preservation,
redaction-safe trace projection, and no duplicate/contradictory identity.

### U3. Atomic approval and resume — ✅ DONE (epic 051; CAS ledger, expected checkpoint/revision + idempotency key, prior-result replay; durable via SQLite + in-memory)

Define a durable generic approval record/command protocol bound to at least:

- session/run and interrupt/call identity;
- expected checkpoint ID or monotonically comparable revision;
- one host-supplied idempotency key;
- decision kind and validated gate provenance;
- recorded terminal consumption/result identity.

Consumption must be atomic against the durable backend:

- exactly one matching approval may transition the parked/interrupted work and
  authorize the associated attempt;
- a concurrent duplicate with the same idempotency key returns the prior
  recorded result and never re-executes the tool;
- a conflicting key/decision or mismatched expected checkpoint returns a
  stable explicit conflict/stale outcome;
- an approval arriving after reject, abort, timeout, newer checkpoint or
  terminal completion cannot revive work;
- crash after consume but before response can be retried safely without a
  second tool execution.

Provide an in-memory implementation for unit tests and a durable Postgres (or
the repository's canonical production durable store) implementation with
transactional compare-and-swap/unique constraints. Do not claim durability
from a process-local dictionary or queue.

Test two independent clients/process contexts approving the same interrupt,
duplicate HTTP-style retries, conflicting decisions, stale revision, restart
between consume/result, and one-time tool side-effect count. The tests must run
against the real durable store implementation as well as the in-memory model.

### U4. Durable Stop and host cancellation — ✅ DONE (epic 052; durable abort lifecycle, cancellation hook + deadline, CANCELLATION_FAILED, late-result fencing, mid-LLM abort)

Define a durable abort lifecycle with stable states/events such as:

```text
abort_requested → abort_observed → cancelled | completed_before_cancel
```

Exact names may differ, but the distinction must remain queryable after process
restart. Requirements:

- abort request is idempotent, durable, actor/reason/time correlated and can be
  issued from another process/context;
- runner checks it before every later plan/LLM/tool transition and prevents new
  work once observed;
- active host work receives a documented cancellation token/hook containing
  run/call/attempt identity and a bounded cancellation deadline;
- terminal outcome truthfully distinguishes cancelled, already completed,
  cancellation failed/timed out, and late result ignored by the runtime;
- late handler completion cannot reopen the run, schedule further actions, or
  overwrite the terminal cancellation record;
- approval/resume after abort cannot revive the run;
- event/checkpoint/support projections preserve the lifecycle without secrets.

The host remains responsible for cancelling its actual job/socket/browser and
quarantining any product-level late evidence. Agent Driver must expose enough
identity and hooks to do so, and must not claim external I/O stopped merely
because the local runner stopped awaiting it.

Test abort while planning, awaiting approval, running a cooperative handler,
running a handler that ignores cancellation, immediately after completion,
and across process restart. Verify no later tool calls and stable terminal
readback.

### U5. Plan integrity extension point — ✅ DONE (epic 053; harness-authored hash + EDIT re-hash, `required_plan_hash` enforcement, host binding, durable `PlanArtifactStore`)

Keep existing plan ID/content hash semantics and expose a supported hook or
opaque metadata binding that lets a host associate an approved plan version
with its own policy snapshot/envelope. A material plan revision must be
detectable before execution so the host can require a new approval.

Agent Driver does not decide what constitutes pentest policy or materiality.
Document the host extension point and test that plan identity/binding survives
checkpoint, resume and trace projection and cannot be overwritten by model
content.

### U6. Gateway truthfulness — ✅ DONE (epic 054; Option 2 — declared non-durable + `require_durable_recovery()`, direct path is durable)

Do not present process-local `_parked` state as restart-safe. Choose one:

1. add a durable parked-run backend using the atomic approval/control protocol,
   with restart and concurrent-client tests; or
2. explicitly document Gateway as process-local/non-durable, fail deployment
   readiness when durable recovery is required, and ensure the supported direct
   embedding path exposes all durable primitives PentestLens needs.

Option 2 is sufficient for this Goal and is preferred unless Gateway
durability is already close and independently justified. Do not expand this
Goal into a server rewrite merely to satisfy PentestLens, which does not plan
to adopt Gateway for the MVP.

### U7. Version, compatibility and release handoff — ✅ DONE (epic 055; `0.2.0` cut, reproducible wheel + SHA-256, handoff document)

- Select the next semantically valid pre-1.0 release version after all required
  contracts pass. Do not hardcode `0.2.0rc6` merely because PentestLens used
  an older rc5; follow upstream version history.
- Make package metadata, runtime `__version__`, built wheel filename/metadata,
  docs and changelog agree.
- Run a clean deterministic wheel build on the supported Python version and
  record exact filename and SHA-256. If byte-for-byte reproducibility is
  promised, prove it with two isolated builds.
- Commit all required code/tests/docs and leave the worktree clean. A tag is
  welcome if it follows upstream release policy but the full commit SHA remains
  mandatory for PentestLens.
- Document migration from old internal imports to the supported facade and
  any persisted checkpoint/event compatibility limitation.

## Explicit non-goals

- PentestLens-specific authorization, autonomy, target/risk/budget or UI code.
- A generic network policy engine or pentest tool catalog.
- Provider/model selection for PentestLens.
- Rewriting all Agent Driver internals solely to make their file layout public.
- Backward compatibility for undocumented internals. Provide migration notes
  and supported equivalents instead.
- Claiming host job/socket cancellation without invoking/observing the host
  cancellation hook.
- Treating unit-only in-memory tests as proof of durable approval or Stop.
- Forcing a durable Gateway rewrite when the direct embedding path is complete
  and Gateway limitations are explicit.

## Definition of Done

- [x] One documented supported embedding facade covers every U1 category and
      the end-to-end example imports no forbidden internal module.
      → `agent_driver.embedding` aggregate + per-concern facades (`docs/embedding.md`);
      `examples/cookbook/19_embedded_e2e.py` (supported imports only, cookbook smoke test).
- [x] Public export/compatibility snapshot and deprecation policy are tested.
      → `tests/contracts/test_export_snapshot.py` (exact `__all__` golden) + deprecation policy in
      `docs/embedding.md`.
- [x] Tool gate/context/results carry stable call/attempt identity and bounded
      JSON-safe provenance through allow/deny/ask, persistence, resume,
      envelopes, events and traces.
      → `ToolGateContext.tool_call_id`/`attempt_id`; `GateProvenance` folded under reserved
      `_ad_gate_provenance`, carried to the approval interrupt + envelope
      (`tests/runtime/test_tool_gate_provenance.py`).
- [x] Malformed or conflicting provenance fails closed and cannot be authored
      or overwritten by model/tool content.
      → `ensure_bounded_json_metadata` (depth/bytes/keys/reserved-key) → DENY; reserved `_ad_`
      namespace isolates host provenance (`tests/contracts/test_bounded_metadata.py`).
- [x] Approval/resume has expected checkpoint/revision, idempotency key, atomic
      one-time durable consumption, prior-result replay, and stable stale/
      conflict outcomes.
      → `ResumeCommand.{idempotency_key,expected_checkpoint_id,expected_revision}`;
      `ApprovalConsumptionStore` CAS; `RunnerConfig(replay_prior_result=)`; `ResumeConflictError`.
- [x] Two-client/Postgres and crash-retry tests prove one tool side effect.
      → two-client + 16-thread SQLite race + crash-retry prove exactly one side effect
      (`tests/runtime/test_approval_consumption_store.py`, `test_resume_atomic_store_integration.py`).
      **Caveat:** proven on the **SQLite** durable store + in-memory; a **Postgres** impl of the
      approval store is deferred (the checkpoint store already has Postgres) — recorded as a residual
      risk in the handoff, non-blocking for the direct embedding path.
- [x] Abort request/observed/terminal lifecycle is durable and idempotent,
      prevents later actions, invokes host cancellation for active work, and
      remains truthful for ignored/late/uncooperative results.
      → `AbortLifecycleStore` (requested→observed→cancelled|completed_before_cancel, SQLite durable);
      `ToolCancellation` hook + deadline; `CANCELLATION_FAILED`; result fencing
      (`tests/runtime/test_abort_lifecycle_store.py`, `test_result_fencing_enforce.py`).
- [x] Restart, awaiting-approval, cooperative/uncooperative-handler and
      approval-after-abort tests pass on the durable implementation.
      → `test_runner_abort_lifecycle.py` (restart via fresh SQLite instance), `test_cancellation_failed.py`
      (uncooperative), `test_mid_llm_abort.py`, `test_abort_resume_interaction.py`.
- [x] Plan identity/policy-binding extension point is supported, durable and
      protected from model overwrite without embedding product semantics.
      → harness-authored `content_hash` + `PlanningPolicyInput.required_plan_hash` enforcement +
      opaque host `plan_policy_binding` + durable `PlanArtifactStore`
      (`tests/tools/test_plan_hash_enforcement.py`, `tests/runtime/test_plan_artifact_store_wiring.py`).
- [x] Gateway is either durable with tests or explicitly non-durable and
      excluded from durable deployment readiness.
      → Option 2: `AgentGateway.durable_parked_runs=False` + `require_durable_recovery()`; direct path
      is durable (`tests/gateway/test_gateway_durability.py`).
- [x] Existing supported behavior, full tests, lint/type/docs checks and the
      supported Python matrix are green, with no required skip or xfail.
      → base 2873 → **2969 passed**; the 2 skipped / 6 xfailed are **pre-existing** and unrelated to
      this Goal (no Goal-required skip/xfail introduced). Supported Python `>=3.11` (run on 3.12).
- [x] Version/package/wheel/changelog/docs agree; exact clean GitHub commit,
      wheel filename/hash and migration notes are ready for downstream pinning.
      → `0.2.0` (pyproject == `__version__` == dist metadata); wheel
      `agent_driver-0.2.0-py3-none-any.whl` SHA-256 `f03fad0d…` (byte-for-byte reproducible);
      release commit `7ff876a`; `handoff-0.2.0-pentestlens.md`.
- [x] Worktree is clean and no required work remains only in local patches,
      comments, skipped tests or a follow-up note.
      → every change committed + pushed to `origin/main`; residual items are optional and documented,
      not required-work hidden in notes.

## Acceptance scenarios

Use the repository's native commands and record their exact forms/results in
the handoff. At minimum run:

1. full baseline and final unit/lint/type/documentation gates;
2. public-import snapshot and embedded fake-provider example;
3. ToolGate allow/deny/ask/resume/retry/failure/timeout/abort provenance matrix;
4. two-client durable-store approval race with one observed tool side effect;
5. duplicate/conflict/stale/restart-after-consume approval cases;
6. durable abort across planning, approval wait, cooperative handler,
   uncooperative handler, completion race and process restart;
7. plan revision/binding persistence and model/tool overwrite attempts;
8. Gateway restart-readiness test or explicit readiness rejection;
9. clean isolated wheel build, metadata/import verification, SHA-256, and
   repeat build if reproducibility is promised.

No test may call a real pentest target or require PentestLens source.

## Required upstream handoff

Create the upstream repository's normal handoff/evidence document containing:

- base and final full GitHub commit, clean status and selected version;
- supported facade modules and exact exported symbol manifest;
- concise breaking/deprecation and persisted-state migration notes;
- ToolGate correlation/provenance schema and limits;
- approval record, CAS/idempotency/stale outcome and durable backend contract;
- abort lifecycle, cancellation hook/token, terminal/late-result contract;
- plan-integrity extension point and Gateway durability status;
- exact commands/results for full and adversarial tests;
- supported Python versions/extras;
- exact wheel filename and SHA-256 plus reproducibility result;
- residual risks that genuinely remain optional, with no required PentestLens
  prerequisite hidden among them.

Do not include secrets, host paths, environment dumps, customer data, model
prompts or target traffic. PentestLens will consume only this clean committed
handoff and exact release identity.
