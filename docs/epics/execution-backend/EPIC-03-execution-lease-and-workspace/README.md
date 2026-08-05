# EPIC-03 — Execution Lease and Workspace Lifecycle

Status: blocked on EPIC-02.

## Outcome

Agent Driver can acquire or attach to one bounded task environment, reuse it
across many ReAct steps, route all claimed workspace operations consistently,
recover its safe reference, and detach or release it according to explicit
ownership.

## Why this epic exists

Remote execution is inefficient and inconsistent if every tool call creates a
fresh environment. Conversely, an implicit forever-running workstation has no
clear ownership, expiry, cleanup, or recovery semantics. A task-scoped lease is
the middle layer: the external backend owns infrastructure, while Agent Driver
owns correct use of the lease inside the run.

## Baseline to inspect

- EPIC-01 and EPIC-02 implementation/handoffs
- `AgentRunInput.workspace_id` and checkpoint serialization
- `workspace_cwd_scope` and filesystem path helpers
- all built-in filesystem tools, including `ls`, `glob`, `grep`, edit, delete,
  and artifact helpers
- artifact store and output-spill paths
- runtime terminal/exception/finalization paths
- `BackgroundRunLease` and durable lifecycle code, to preserve separation
- subagent workspace isolation and handoff code

## In scope

1. Add validated `ExecutionLeaseRequest`, `ExecutionLeaseRef`,
   `ExecutionLease`, ownership, state, generation, expiry, and lifecycle
   receipts.
2. Support idempotent acquire and attach with stable request identity.
3. Reuse one accepted lease across tool calls and ReAct steps in the run.
4. Allow an explicit host-owned lease to be attached across runs without Agent
   Driver claiming ownership of its provisioning or persistence.
5. Persist only a safe lease reference and generation needed for checkpoint or
   resume. Credentials remain in backend/host configuration.
6. Route every built-in file operation that claims backend workspace support.
   Unsupported operations are hidden/denied truthfully; no local fallback is
   allowed after a remote lease has been selected.
7. Define backend-relative path validation, workspace root, writable roots, and
   traversal/symlink expectations without assuming a local `Path.resolve()` can
   validate remote state.
8. Integrate backend-produced artifacts with existing Agent Driver artifact
   references and output bounds.
9. Detach host-owned leases and release runtime-owned leases on all terminal,
   exception, timeout, cancellation, and failed-acquire paths.
10. Emit separate queue, acquire, ready, detach/release, and teardown-pending
    timings/receipts.

## Non-goals

- Allocating a workspace per product user or sharing one among users.
- Fleet capacity, fairness, quotas, autoscaling, or image cache management.
- Defining concrete container, VM, or cluster APIs.
- Long-running command event streaming and hard teardown proof; EPIC-04 owns
  execution-job lifecycle.
- Conversation memory and skill selection.
- Assuming task lease lifetime equals conversation or user lifetime.

## Design constraints

- Use a new `ExecutionLease`; do not overload `BackgroundRunLease`.
- `workspace_id` is correlation only and cannot attach a lease by itself.
- Lease references are non-secret, generation-bound, and safe for durable state.
- A stale generation fails closed and cannot receive new work.
- Runtime-owned cleanup lives in `finally`-equivalent paths and is idempotent.
- Host-owned means detach only; Agent Driver must not destroy external state.
- A remote-selected run never silently falls back to local disk or process.
- Subagents inherit, share, or isolate execution leases only through an explicit
  policy; the default must be documented and tested.

## Work packages

### A. Lease contracts and manager

Implement lifecycle states, ownership, request idempotency, generation/expiry,
safe durable reference, and typed receipts. Decide whether management belongs
in the runner or a small execution session component; do not put it in tools.

### B. Run/checkpoint integration

Acquire/attach before the first operation that needs the environment, or eagerly
when configured policy requires it. Restore only by safe attach/snapshot; never
assume a previous in-process object survived resume.

### C. Complete workspace routing

Audit every filesystem builtin and path helper. Route read/write/edit/delete,
enumeration, glob, grep, stat/metadata, and artifact collection as supported.
Make partial support visible through capabilities.

### D. Artifact bridge and bounds

Map backend artifact descriptors to existing public artifact references. Prove
digest/size identity, bounded previews, and no implicit full-content load.

### E. Cleanup and ownership

Cover success, model error, tool error, timeout, abort, interrupt/pause,
checkpoint, process restart, attach failure, and duplicate release. A paused run
must have an explicit lease policy rather than accidental retention.

## Acceptance scenarios

1. A multi-step run performs several commands and file operations against one
   lease ID/generation; acquire occurs once.
2. Two runs never share a runtime-owned lease accidentally.
3. A host-owned lease can be attached and detached across runs without a
   destructive release call.
4. A runtime-owned lease receives exactly one effective release across normal,
   exception, timeout, and cancellation paths; duplicate requests are safe.
5. Resume attaches by safe reference and verifies generation/capabilities before
   new work. A stale/expired lease fails closed with a typed recovery outcome.
6. Remote `read`, `write`, edit, delete, list, glob, grep, and artifact behavior
   never touches an unrelated local workspace.
7. A backend lacking one file capability causes that tool to be withheld or
   denied; it does not use local fallback.
8. Traversal and symlink-escape attempts are rejected according to the backend
   path contract and appear as bounded governed failures.
9. A large output/file becomes a digest- and size-bearing artifact reference;
   only the bounded preview enters model context.
10. Lease queue/acquire/ready/release timings are independently observable.
11. The documented subagent lease policy is enforced in tests.

## Definition of done

- One lease safely spans the intended loop and all claimed workspace tools.
- Ownership, resume, expiry, artifacts, path safety, and cleanup are proven.
- Local and ACP compatibility remains green.
- Public/durable contract and failure-injection tests pass.
- SemVer impact and EPIC-04 predecessor evidence are recorded.

