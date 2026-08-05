# Goal — EPIC-04 Execution Jobs, Events, Control, and Recovery

## Objective

Implement reconnectable execution jobs with ordered bounded events, stable
snapshots, truthful controls, generation fencing, and separate teardown proof.

## Mandatory context

Read the package docs, EPIC-01 through EPIC-03 handoffs, this epic's
`README.md`, runtime event/replay/checkpoint code, live-message and Stop docs,
tool cancellation/progress code, and terminal-phase contracts.

## Predecessor gate

- EPIC-03 lease ownership, safe references, complete workspace routing, and
  cleanup tests are green.
- Backend results already carry stable run/attempt/tool/lease/request identity.
- Remote-selected execution cannot fall back to local paths.

## Required deliverables

- Public execution job/event/snapshot/control/teardown contracts.
- Start/lookup/observe/snapshot/control/terminal lifecycle.
- Runtime projection, reconnect, fencing, Stop, recovery, timeout and timing
  integration.
- Deterministic duplicate/loss/restart/late-result/failure tests.
- Docs, example, changelog and SemVer decision.

## Constraints

- No exactly-once claim without proof.
- No conflation of run cancellation, execution termination, and teardown.
- No unbounded raw remote output in runtime state.
- No required PTY, SSH, or transport implementation.

## Terminal condition

Finish only when the failure and recovery matrix is green through the real
runner and the handoff records exact guarantees, no-claims, evidence, and the
green predecessor gate for EPIC-05.

