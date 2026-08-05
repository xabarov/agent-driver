# Goal — EPIC-03 Execution Lease and Workspace Lifecycle

## Objective

Implement generation-bound execution leases that are acquired or attached once,
reused across the agent loop, recovered safely, and detached or released on all
paths, with complete routing for every claimed workspace operation.

## Mandatory context

Read the package docs, EPIC-01/02 handoffs, this epic's `README.md`, runtime
checkpoint/finalization code, all built-in filesystem/path/artifact code,
durable worker-lease contracts, and subagent workspace-isolation code.

## Predecessor gate

- EPIC-02 snapshot and tool-requirement enforcement are green above and below
  the model.
- The configured backend is immutable for an active attempt.
- Local and ACP compatibility suites are green.

## Required deliverables

- Public lease/request/reference/receipt contracts.
- Acquire/attach/reuse/detach/release integration.
- Safe checkpoint/resume reference.
- Complete capability-aware workspace routing.
- Artifact bridge, path-safety, ownership, subagent-policy and cleanup tests.
- Docs, example, changelog and SemVer decision.

## Constraints

- Do not overload `workspace_id` or `BackgroundRunLease`.
- Do not persist backend credentials.
- Do not silently fall back to local execution after selecting a remote lease.
- Do not implement a concrete fleet or container manager in core.

## Terminal condition

Finish only when lifecycle cleanup and complete workspace-routing acceptance
scenarios pass under injected failures and the handoff records exact evidence,
limitations, and the green predecessor gate for EPIC-04.

