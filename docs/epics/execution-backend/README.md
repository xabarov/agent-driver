# Execution Backends and Task Workspaces

Status: ready for sequential implementation, one epic at a time.

This package defines the Agent Driver work needed to run governed tools in a
prepared local or remote task workspace. It is deliberately domain-neutral and
self-contained: an implementation agent needs only this repository and the
documents linked below.

The target is one decision loop with a replaceable execution plane:

```text
host application
    |
    | selects backend and supplies policy/configuration
    v
Agent Driver runtime and governed tool loop
    |
    | typed ExecutionBackend contract
    v
local process | task container | remote worker | another host-owned adapter
```

The execution plane is not a second agent. It does not plan, select tools,
approve actions, interpret output, or own conversation memory. Agent Driver
keeps those responsibilities and calls the backend only after the normal tool
policy, guardrail, and approval pipeline has allowed an operation.

## Outcome

After all epics are complete, an embedder can:

- inject a supported `ExecutionBackend` through public Agent Driver APIs;
- acquire or attach to one execution lease and reuse it across many ReAct steps;
- expose only capabilities that the selected environment actually provides;
- run command and workspace-aware file operations without assuming local disk;
- receive bounded results, artifact references, ordered events, and timings;
- reconnect to an in-flight execution and apply supported controls;
- distinguish cooperative cancellation from confirmed hard teardown;
- qualify a backend with a deterministic, reusable compatibility suite; and
- keep today's local execution behavior through a compatibility adapter.

## Current baseline

The repository already contains useful but separate seams:

- `agent_driver.tools.context.AsyncCommandRunner` and
  `command_runner_scope()` route the built-in `bash` tool for ACP sessions.
- `agent_driver.tools.context.AsyncFileIO` and `fs_io_scope()` route text reads
  and writes, while path validation still assumes a local workspace.
- `agent_driver.fs.FileBackend` models a synchronous text store, but it is not
  an asynchronous task-environment lifecycle.
- `AgentRunInput.workspace_id` is a host-facing logical identifier. It is not
  an execution lease, capability claim, or backend credential.
- `workspace_cwd` in `app_metadata` selects a local path and is projected into
  the prompt. It cannot describe or prove a remote environment.
- `RunAbortHandle` and `ToolCancellation` provide process-local/cooperative
  cancellation. Current Stop semantics intentionally do not promise remote
  process-tree or container teardown.
- runtime and harness event contracts already provide stable run/attempt/seq
  identities, replay, result fencing, artifacts, and durable control concepts.
- `BackgroundRunLease` leases a worker for durable run supervision. It must not
  be reused as an execution-environment lease.
- `HarnessAdapter*` contracts project Agent Driver state to host protocols.
  They are separate from the tool execution backend defined here.

The epics converge these seams instead of building a parallel runtime.

## Ownership boundary

| Agent Driver owns | Backend or host owns |
| --- | --- |
| Public backend-neutral contracts | Building and patching runtime images |
| Tool-policy and approval ordering | Container, VM, or worker provisioning |
| Lease use inside the agent loop | Tenant/user placement and fleet scheduling |
| Stable execution identities and fencing | Network topology and egress enforcement |
| Bounded observations and artifact references | Secrets delivery and credential brokers |
| Event/control/recovery semantics | OS isolation and resource enforcement |
| Capability truthfulness and prompt projection | Domain tools, skills, and environment contents |
| Compatibility suite and deterministic fake | Production backend implementation and operations |

Agent Driver may ship a local compatibility backend and a deterministic fake.
A production container or remote-worker adapter can live in another package.
It must pass the compatibility suite before a host relies on its claims.

## Global invariants

Every epic must preserve these rules:

1. The host selects the backend. Model text, tool arguments, tool output, and
   resumed untrusted state cannot switch the backend or expand its authority.
2. Backend execution happens only after the existing policy, guardrail,
   approval, timeout, and budget checks applicable to the tool call.
3. One lease may serve many tool calls and ReAct steps. A container per tool
   call is not the default contract.
4. Lease ownership is explicit. Agent Driver releases leases it owns and only
   detaches from host-owned leases.
5. A logical `workspace_id`, local `workspace_cwd`, worker lease, and execution
   lease are different identifiers and are never treated as interchangeable.
6. Capabilities are observed backend facts with a generation/revision, not
   prompt promises. Installed programs do not automatically become model tools.
7. Output is bounded before it enters model context. Large output becomes an
   artifact reference with integrity and size metadata.
8. Every mutating request carries stable identity and supports duplicate-safe
   retry or explicitly reports that it cannot.
9. Late results from a stale attempt, lease generation, or execution generation
   are fenced and cannot commit normal tool results.
10. `control accepted`, `control applied`, `execution terminal`, and `lease
    released/teardown confirmed` are distinct facts.
11. Unsupported behavior is reported as unsupported. A backend never upgrades
    cooperative cancellation into a hard-kill claim.
12. No secret values, raw credentials, or unrestricted environment dumps enter
    contracts, events, logs, prompts, snapshots, or compatibility artifacts.

## Epic sequence

| Epic | Result | Depends on |
| --- | --- | --- |
| [EPIC-01](EPIC-01-execution-backend-contract/README.md) | Public contract, errors, injection seam, and local compatibility adapter | Current `main` |
| [EPIC-02](EPIC-02-capabilities-and-routing/README.md) | Truthful capability snapshot, backend selection boundary, environment brief | EPIC-01 |
| [EPIC-03](EPIC-03-execution-lease-and-workspace/README.md) | Acquire/attach/reuse/detach/release lifecycle and complete workspace routing | EPIC-02 |
| [EPIC-04](EPIC-04-jobs-events-and-control/README.md) | Long-running jobs, ordered replay, controls, fencing, recovery, teardown receipts | EPIC-03 |
| [EPIC-05](EPIC-05-compatibility-kit-and-release/README.md) | Backend compliance kit, failure matrix, migration guide, and supported release surface | EPIC-04 |

Do not combine adjacent epics into one implementation goal without an explicit
decision. Each epic has its own `GOAL.md`, predecessor gate, acceptance
scenarios, and handoff requirements.

## Reading order for an implementation agent

1. Repository `CLAUDE.md`.
2. This page.
3. [Target contract](TARGET_CONTRACT.md).
4. [Epic execution standard](EPIC_STANDARD.md).
5. `docs/embedding.md`, `docs/runtime.md`, `docs/sdk-tools.md`,
   `docs/builtin-tools.md`, `docs/acp.md`, `docs/live-message-controls.md`, and
   `docs/terminal-phase-contract.md`.
6. The selected epic's `README.md` and `GOAL.md`.
7. The current implementation and tests named by that epic. Documentation is
   a design target; current code remains the source of truth for the baseline.

## Promotion and change control

- Record material contract choices in `TARGET_CONTRACT.md` before or with code.
- Prefer additive public contracts and adapters over changing existing tool
  handler signatures throughout the repository.
- Update public export snapshots, schema snapshots, docs, examples, and the
  changelog whenever a new public symbol or wire field ships.
- Classify SemVer impact before closing each epic. Documentation-only planning
  does not require a version change; implemented public capability normally
  does.
- Preserve the ACP behavior while migrating it to the common execution seam.
- If evidence invalidates a target decision, update this package and explain
  the replacement; do not silently implement a different architecture.

