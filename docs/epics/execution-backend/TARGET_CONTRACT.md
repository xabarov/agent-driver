# Target Execution Contract

This page fixes the architectural boundary and minimum semantics shared by all
execution-backend epics. Exact Python names may be refined during EPIC-01, but a
change to the responsibilities or guarantees below is an architecture decision
and must be recorded here.

## Terms

- **Execution backend**: a host-selected adapter that performs command and file
  operations in a prepared environment.
- **Execution lease**: a generation-bound right to use one task workspace for a
  bounded period. It may map to a local directory, container, VM, or remote
  worker.
- **Workspace**: the filesystem view associated with a lease.
- **Execution**: one command or other backend operation within a lease.
- **Capability snapshot**: versioned backend facts observed for a particular
  environment generation.
- **Environment brief**: a bounded, redacted model-facing projection derived
  from a capability snapshot. It is guidance, not an authorization boundary.
- **Control receipt**: evidence that a requested action was accepted, rejected,
  or applied. Acceptance alone does not prove effect.
- **Teardown receipt**: evidence of the strongest cleanup fact the backend can
  honestly claim.

## Separation from existing concepts

| Existing concept | Meaning | Relationship to this contract |
| --- | --- | --- |
| `AgentRunInput.workspace_id` | Host's logical workspace/session grouping | May be included as correlation metadata; never used as a lease token |
| `workspace_cwd` | Local path hint | Used by the local adapter; not a remote capability proof |
| `BackgroundRunLease` | Worker ownership for durable run supervision | Remains separate from `ExecutionLease` |
| `HarnessAdapter*` | Projection of runs to host protocols | May expose execution summaries but does not execute tools |
| `ToolManifest` | Model tool policy and governance metadata | Remains the source of tool risk/approval semantics |
| `RunAbortHandle` | Run-level abort signal | Can initiate backend control, but does not by itself prove hard teardown |

## Public surface

The final API should be importable from a supported facade, with embedding
essentials re-exported from `agent_driver.embedding`. The target shape is one
backend protocol plus validated contracts; it is not a collection of untyped
callbacks or `app_metadata` dictionaries.

An illustrative protocol is:

```python
class ExecutionBackend(Protocol):
    async def acquire(self, request: ExecutionLeaseRequest) -> ExecutionLease: ...
    async def attach(self, ref: ExecutionLeaseRef) -> ExecutionLease: ...
    async def capabilities(self, lease: ExecutionLeaseRef) -> CapabilitySnapshot: ...
    async def start(self, lease: ExecutionLeaseRef, request: ExecutionRequest) -> ExecutionHandle: ...
    async def events(self, handle: ExecutionHandle, *, after: str | None) -> ExecutionEventPage: ...
    async def snapshot(self, handle: ExecutionHandle) -> ExecutionSnapshot: ...
    async def control(self, handle: ExecutionHandle, request: ExecutionControlRequest) -> ControlReceipt: ...
    async def workspace(self, lease: ExecutionLeaseRef) -> AsyncWorkspace: ...
    async def detach(self, lease: ExecutionLeaseRef) -> LeaseReceipt: ...
    async def release(self, lease: ExecutionLeaseRef) -> TeardownReceipt: ...
```

This is a semantic target, not permission to publish a needlessly broad first
version. EPIC-01 should keep the smallest coherent API and add later methods in
the epic that proves their behavior. Unsupported optional operations must be
discoverable through capabilities and return a typed unsupported error.

## Required identities

The contracts must keep these identities distinct:

- `run_id` and `attempt_id`: the Agent Driver run attempt;
- `tool_call_id`: the model/tool-governance call;
- `backend_id`: the host-selected backend implementation/route;
- `lease_id` and `lease_generation`: the task environment instance;
- `execution_id` and `execution_generation`: one backend operation;
- `request_id` or idempotency key: one mutating backend request;
- event `cursor` and monotonic sequence within an execution;
- artifact identity and digest.

Every event, result, control receipt, and teardown receipt must carry enough
identity to reject a response from an obsolete attempt or generation.

## Lease request and ownership

An execution lease request contains only backend-neutral needs, for example:

- run/attempt correlation;
- an opaque host-selected environment/profile reference;
- required capability names and minimum revisions;
- requested lifetime and resource bounds;
- optional logical workspace correlation;
- an idempotency key;
- redacted metadata.

It does not contain model-selected image names, raw credentials, unrestricted
shell setup scripts, or network policy authored by model output.

The returned lease declares whether it is:

- **runtime-owned**: Agent Driver must release it on every terminal path; or
- **host-owned**: Agent Driver may attach/detach but must not destroy it.

Acquire and attach are idempotent for the same key. Reuse is allowed only while
backend identity, lease generation, expiry, and required capabilities match.

## Capability snapshot

The snapshot is a validated, bounded, redacted contract. At minimum it reports:

- backend ID, contract version, environment revision, and observed-at time;
- lease generation to which the snapshot applies;
- supported command, file, event, control, reconnect, artifact, and teardown
  semantics;
- workspace path model and writable roots without exposing host secrets;
- bounded program/runtime inventory sufficient for tool availability decisions;
- declared output, timeout, and resource limits;
- degradation/unknown reasons and snapshot freshness.

Capability states use at least `supported`, `unsupported`, `degraded`, and
`unknown`. Missing evidence is `unknown`, never `supported`.

The snapshot is not a tool registry. A binary may be installed but hidden from
the model; a registered tool may be withheld if its backend requirements are
not satisfied.

## Environment brief

Agent Driver may add a concise environment brief to request-only model context.
It must be derived from the accepted snapshot, capped by a deterministic size
budget, redact secret-like values, and include the snapshot revision. It may
describe available tools/programs, workspace conventions, limits, and known
gaps. It must not contain executable setup instructions from untrusted output.

Prompt text never expands policy. The executor checks the actual lease and
capability snapshot again before dispatch.

### Prepared environment without repeated setup

The external backend gives an environment revision to a prepared filesystem and
program set. Agent Driver caches and reuses the accepted snapshot for the lease,
so the model can see what is already available instead of repeatedly trying to
install or rediscover it.

The responsibilities stay separate:

- the backend reports the observed environment/program revision;
- Agent Driver owns the registered tools, skills, prompts, and their normal
  model-facing projection;
- capability requirements connect a registered tool to the environment facts
  it needs; and
- a missing requirement withholds or fails the tool explicitly. It does not
  trigger an automatic installation fallback in core.

Skill and prompt contents do not belong in the backend snapshot. If useful, a
combined brief may name their host-supplied revisions without copying secrets or
unbounded content.

## Command and workspace behavior

- The built-in `bash` path routes through the execution backend when configured.
- Every built-in file tool that claims remote-workspace support routes all
  relevant operations through the backend. It must not validate remotely and
  then read or enumerate the local filesystem by accident.
- Paths are backend-relative or use an explicit validated workspace root.
  Backend paths must never be resolved against an unrelated local process path.
- The local compatibility backend preserves current local jail and command
  policy behavior.
- Policy and approval remain above dispatch. The backend receives an already
  authorized, bounded request plus correlation metadata; it does not replace
  Agent Driver governance.

## Results, output, and artifacts

An execution result contains a typed terminal state, exit/status information,
start/end times, applied bounds, truncation facts, and references to full
artifacts when available. Standard output and error are bounded before entering
runtime events, traces, checkpoints, or model context.

Large or binary results use content-addressed artifacts with at least digest,
size, media type when known, backend/lease/execution identity, and a safe
retrieval reference. A reference is not proof that content was inspected.

## Events and reconnect

Execution events are ordered within an execution and replayable from a stable
cursor. The event page states whether history is complete, compacted, or has a
gap. Duplicate delivery is allowed; conflicting content for the same identity
and sequence is not.

After transport loss, Agent Driver queries a snapshot and resumes from the last
committed cursor. It does not rerun an unknown mutating operation merely because
the response was lost. The backend must support idempotent lookup or return an
explicit indeterminate state for manual/host resolution.

## Controls and teardown

Minimum controls are capability-driven. The contract must distinguish:

- cancellation requested;
- cancellation accepted;
- cooperative stop observed;
- process terminated;
- process tree terminated;
- execution environment destroyed;
- cleanup unknown or failed.

Pause, resume, signal, or PTY features are optional and out of the initial
contract unless an acceptance scenario proves demand. Stop can request the
strongest supported control, but terminal run state and teardown confirmation
remain separate receipts.

## Failure classes

Public typed failures must distinguish at least:

- unsupported capability;
- invalid/stale lease or generation;
- policy/precondition rejection before dispatch;
- queue/acquire timeout;
- execution timeout;
- transport interruption with known execution identity;
- indeterminate dispatch/result;
- output/artifact limit exceeded;
- cancellation or teardown failure;
- backend protocol violation.

Error text is redaction-safe, bounded, and suitable for traces. Raw remote
payloads remain outside model context and support artifacts unless sanitized.

## Lifecycle timing

Receipts and events expose enough monotonic or timestamp data to separate:

`queued -> lease acquiring -> environment ready -> execution started -> first
output -> terminal -> release requested -> teardown confirmed`.

This makes cold-start, queue, command, reconnect, and cleanup delays measurable
without inferring them from one total duration.

## Initial exclusions

The target does not require Agent Driver to provide:

- a container engine, cluster scheduler, or remote-worker service;
- image construction, package installation, or update policy;
- user-to-workstation assignment or multi-tenant fleet scheduling;
- network/egress enforcement or credential brokerage;
- domain-specific tools, skills, policies, or evidence interpretation;
- an unrestricted interactive terminal, PTY, SSH, desktop, or browser session;
- a second planning agent inside the execution environment;
- exactly-once side effects when a backend can only offer at-least-once or an
  indeterminate result.

## Deployment mapping (non-normative)

The same contract supports different infrastructure without putting topology
decisions in Agent Driver:

| Infrastructure shape | Execution-contract view |
| --- | --- |
| Current local directory/process | Local compatibility backend and a bounded local lease |
| Fresh container for one task/run | Runtime-owned lease; reused across its tool calls; released at terminal |
| Long-lived prepared workstation | Host-owned lease; Agent Driver attaches and detaches, but does not destroy it |
| Remote worker pool | Host/backend selects placement; Agent Driver sees only backend, lease, generation, and capabilities |

The host decides which shape to use and how long it should persist. Agent Driver
does not infer that decision from a user ID, workspace ID, tool argument, or
model message.

## EPIC-01 design decision (recorded 2026-08-05, from baseline inventory)

This section records the concrete Python realization chosen for EPIC-01. It
refines names only; the responsibilities/guarantees above are unchanged.

### Baseline reality that shapes the seam

- Two run-scoped `ContextVar` seams already exist in `agent_driver/tools/context.py`
  and are the ONLY current injection points:
  - `AsyncCommandRunner.run_command(command, *, cwd: str, timeout_seconds: float)
    -> {stdout, stderr, timed_out, exit_code}` (consumed by `bash`, `shell.py`).
  - `AsyncFileIO.read_text(path)->str` / `write_text(path, content)->None`
    (consumed by `read_file`/`file_write`/`file_edit`/`file_patch` via
    `read_text_routed`/`write_text_routed` in `filesystem/_paths.py`).
- cwd resolution + jail + `.exists()/.is_file()` gating are a THIRD, disk-bound
  layer (`workspace_cwd_scope`, `_resolve_cwd`, `_ensure_within_workspace_jail`)
  that runs BEFORE the byte/command seam and is not injectable. Output bounds /
  truncation / size-guard live in the tool handlers, not the seam.
- Coverage gap: `notebook_edit`, `glob_search`, `grep_search`, `artifact_*` bypass
  `AsyncFileIO` (direct disk). ACP is the only current consumer of the scopes
  (`adapters/acp/server.py` enters both around `agent.run`).

### Decisions

1. **Path resolution stays local/disk-bound in EPIC-01** (byte/command-only seam,
   as today). Full backend-owned workspace routing (paths, discovery, the bypass
   tools) is EPIC-03. The local backend preserves current cwd/jail/existence
   behavior by wrapping today's code, not reimplementing it.

2. **Public surface** — a new supported subpackage `agent_driver/execution/`
   (facade `agent_driver.execution`), embedding-essentials re-exported from
   `agent_driver.embedding`. Validated contracts live in `agent_driver/contracts/
   execution.py` (subclassing `ContractModel`, `extra="forbid"`), constant
   `EXECUTION_SCHEMA_VERSION = "agent_driver.execution.v1"`.

3. **`ExecutionBackend` protocol (async, minimal for EPIC-01):**
   - `backend_id: str` (property)
   - `async run_command(request: ExecutionCommandRequest) -> ExecutionCommandResult`
   - `async read_text(request: ExecutionReadRequest) -> ExecutionReadResult`
   - `async write_text(request: ExecutionWriteRequest) -> ExecutionWriteResult`
   Reserved-but-not-yet-methods (defined as contracts / vocabulary only): lease
   (`ExecutionLeaseRequest/Ref/Receipt`), `CapabilitySnapshot` +
   `CapabilityState{supported,unsupported,degraded,unknown}`, events/control,
   teardown. `capabilities()`/lease methods land in EPIC-02/03/04.

4. **Typed results, never raw dict.** `ExecutionCommandResult` carries
   `stdout/stderr/exit_code/timed_out` + applied bounds + truncation facts +
   identity; formalizes the current bash dict. Read/write results are typed too.

5. **Identity on every request** (`ExecutionIdentity`): `backend_id`, `run_id`,
   `attempt_id`, `tool_call_id`, `request_id` (idempotency key). Enough to fence a
   stale response. Sourced from the per-call `tool_call_context` at dispatch time.

6. **Typed failures** — an `ExecutionError` hierarchy (bounded, redaction-safe,
   categorizable without message parsing): at least `UnsupportedCapabilityError`,
   `ExecutionTimeoutError`, `ExecutionTransportError`, `IndeterminateExecutionError`,
   `OutputLimitExceededError`, `BackendProtocolError`. Lease/stale-generation
   classes are reserved for later epics.

7. **Injection = a `RunnerConfig` field** `execution_backend: ExecutionBackend |
   None` (mirrors `tool_executor`), resolved `None -> LocalExecutionBackend()` into
   a non-optional `RunnerDeps.execution_backend` in `SingleAgentRunner.__init__`.
   PLUS an optional per-run `run(..., execution_backend=...)` param (parallel to
   `abort_handle`/`tool_gate`) so ACP keeps per-session routing. Model text / tool
   args / metadata can NEVER select the backend.

8. **Wiring via thin adapters to the existing scopes** (tools stay UNCHANGED):
   when a backend is configured, `_drive_steps` enters
   `command_runner_scope(_BackendCommandRunner(backend))` and
   `fs_io_scope(_BackendFileIO(backend))` beside `workspace_cwd_scope`. The
   adapters read per-call identity from `get_tool_call_context()`, build the typed
   request, call the backend, and map the typed result back to the dict/text the
   handlers already expect. Governance stays above dispatch (`GovernedToolExecutor`
   ladder in `executor/`); the adapter is only reached from inside an
   already-authorized handler body, so DENY/INTERRUPT/BLOCK/abort never hit it.

9. **`LocalExecutionBackend`** wraps current local subprocess + disk behavior
   (exact `{stdout,stderr,timed_out,exit_code}`, `read_text_with_size_guard`,
   `write_text` UTF-8). **`FakeExecutionBackend`** — deterministic, scripted
   results for tests (minimal; the reusable compliance kit is EPIC-05).

10. **ACP migration** — publish `CompositeExecutionBackend(file_io=AsyncFileIO,
    command_runner=AsyncCommandRunner)` in `agent_driver.execution`; `server.py`
    passes it via `run(execution_backend=...)` and drops
    `from agent_driver.tools.context import command_runner_scope, fs_io_scope`.
    `AcpClientFileIO`/`AcpTerminalRunner` are unchanged (already implement the two
    protocols); the composite drives them through the raw scopes internally.

11. **SemVer / snapshots** — additive public surface ⇒ minor bump `0.4.0 -> 0.5.0`,
    `CHANGELOG [Unreleased] → Added`. Update `test_export_snapshot.py` golden sets,
    `test_public_exports.py` required subsets, `test_embedding_namespace.py`
    identity/essentials, `test_schema_snapshots.py` field + JSON-schema snapshots.

### Explicitly deferred (not EPIC-01)

Lease acquire/attach/reuse/release (EPIC-03), capability snapshot + routing
(EPIC-02), events/reconnect/control/teardown (EPIC-04), full workspace routing
(paths, `ls/glob/grep/delete/edit`, the bypass tools) (EPIC-03), the reusable
compliance kit (EPIC-05). These names are reserved above but not implemented now.

### Revision (2026-08-05, during WP-D/E implementation)

Two points from decisions 7 and 10 were refined against the real code:

- **Injection (decision 7) — CONFIRMED both paths.** `RunnerConfig.execution_backend`
  is the config-level default; a per-run override threads through `Agent.run` /
  `SingleAgentRunner.run(execution_backend=...)` → `_init_context` →
  `RunContext.execution_backend` (beside `abort_handle`/`tool_gate`, for the same
  "live object, not a JSON transport field" reason). `_drive_steps` resolves
  `context.execution_backend or config.execution_backend`. Per-run is a HOST
  concern (e.g. one prepared env per session), distinct from model-driven lease
  selection, which stays EPIC-03.

- **ACP cutover (decision 10) — DEFERRED to EPIC-02.** The baseline is more
  capable than the inventory implied: `_command_runner(session)` and
  `_file_io(session)` are **independently** `None` (client may advertise terminal
  without fs, or vice-versa), each with a per-capability **local fallback** via a
  `None` scope; and tools run in BOTH the initial `run()` leg and the
  `resume()` leg (the scopes currently wrap both). A faithful single-backend
  cutover therefore needs (a) a per-capability fallback in
  `CompositeExecutionBackend` (missing half → delegate to local, not raise) and
  (b) `execution_backend` threaded through `resume()` as well. Both belong with
  EPIC-02's capability model. For EPIC-01, `CompositeExecutionBackend` ships as the
  supported **shim primitive**, and ACP keeps using the public, supported
  `command_runner_scope`/`fs_io_scope` (which the new adapters themselves use) —
  so ACP stays green (acceptance scenario 8) with no divergence, only a
  not-yet-unified call site. This satisfies the epic's "provide compatibility
  shims for ACP … OR migrate ACP" as the shim branch.

## EPIC-02 design decision (recorded 2026-08-05)

Work Package A (capability contracts + routing helpers), landed on
`feat/execution-backend-epic02`:

1. **`ExecutionCapabilitySnapshot` replaces the reserved minimal
   `CapabilitySnapshot`.** The EPIC-01 placeholder shipped in 0.5.0 as reserved +
   unused; it is now removed and superseded by the fuller model (per-capability
   `CapabilityStatus{state,reason}` map keyed by a typed `CapabilityName`, bounded
   `ProgramInfo` inventory, `environment_revision`, `lease_generation`, `digest`,
   `observed_at`, bounded+secret-rejecting `metadata`). New capability wire
   version `EXECUTION_CAPABILITY_SCHEMA_VERSION = "agent_driver.execution.capability.v1"`
   (distinct from the request/result `EXECUTION_SCHEMA_VERSION`). Additive minor.

2. **`CapabilityName` vocabulary:** command, file_read, file_write, event,
   control, artifact, reconnect, timeout, output, resource, teardown. Distinct
   from `CapabilitySettings` (runner knobs), `HarnessCapabilityPack` (product
   validation), and `HarnessAdapterCapability` (adapter features) — no reuse.

3. **Optional `CapabilityAwareBackend(ExecutionBackend, Protocol)`** adds
   `async capabilities() -> ExecutionCapabilitySnapshot`. Keeping it a SEPARATE
   runtime_checkable protocol (not a new required method on `ExecutionBackend`)
   means EPIC-01 minimal backends and external adapters stay valid; a backend
   that does not report capabilities is treated as all-`UNKNOWN`.

4. **`resolve_capability_snapshot(backend)` fails safe:** missing `capabilities`,
   a raised handshake, or a wrong return type all yield an all-`UNKNOWN`
   snapshot. `unknown` never satisfies a hard requirement.

5. **`ToolExecutionRequirement{required, hard}` + `check_requirement` +
   `RequirementCheck`:** a HARD requirement is satisfied only when every named
   capability is `SUPPORTED` (`DEGRADED`/`UNSUPPORTED`/`UNKNOWN` fail closed); a
   SOFT requirement never blocks but still surfaces unmet capabilities. Pure,
   deterministic, snapshot-only.

6. **`derive_environment_brief` → `EnvironmentBrief`:** deterministic, sorted,
   char-bounded projection for request-only context; `capability_revision` =
   `digest or environment_revision`; unknown/unsupported capabilities are omitted
   (never described as available); over-budget programs/limitations are trimmed
   from the tail with `truncated=True`.

Remaining WPs (deferred within EPIC-02): B backend handshake + cache/freshness;
C tool-requirement routing in `llm_step/build.py` (pre-model filter) + a
pre-dispatch re-check in `executor/governed.py` (anti-TOCTOU); D brief injection
via `request_only_context`; E redaction-safe selection diagnostics/timings.

## EPIC-03 design decision (recorded 2026-08-05)

Work Package A (lease contracts + manager + optional protocol), on
`feat/execution-backend-epic03`. Grounded in a full baseline inventory.

1. **New `ExecutionLease` vocabulary in `contracts/execution_lease.py`** —
   `ExecutionLeaseRequest`, `ExecutionLeaseRef` (the ONLY durable, non-secret,
   generation-bound reference), `ExecutionLease` (live: ref+state+expiry+
   `WorkspacePaths`+capability snapshot), `LeaseReceipt`, enums
   `LeaseOwnership{RUNTIME_OWNED,HOST_OWNED}` / `LeaseState` /
   `LeaseLifecyclePhase`, `EXECUTION_LEASE_SCHEMA_VERSION`. Distinct from
   `BackgroundRunLease` (worker-ownership of a run) — confirmed a different axis.
   Metadata rejects secret-like keys; refs `fences()` a stale generation.

2. **Optional `LeaseCapableBackend(ExecutionBackend, Protocol)`** adds
   `acquire_lease`/`attach_lease`/`release_lease`/`detach_lease` (all idempotent),
   kept separate so EPIC-01/02 minimal backends stay valid (a backend without
   them simply cannot be leased — never a silent local fallback).

3. **`ExecutionLeaseManager`** (a small run session component, NOT a tool): one
   lease per run, idempotent acquire/attach per `request_id`, reuse across steps,
   `attach_by_ref` for resume (stale generation fails closed), and `close()` that
   releases (runtime-owned) or detaches (host-owned) exactly once and swallows
   backend teardown errors into a `teardown_pending` receipt — safe from a
   `finally`-equivalent path. Non-lease backend → `UnsupportedCapabilityError`;
   non-READY lease → `LeaseNotUsableError` (fail closed).

Confirmed integration seams for the remaining WPs (from inventory):
- **B (run/checkpoint):** `ExecutionLease` as a 4th live field on `RunContext`
  (like `execution_backend`); acquire/attach in the `_drive_steps` `ExitStack`
  and add a lease-scoped ContextVar next to `capability_snapshot_scope`; the
  **outer `finally` at `runner.py:255`** is the authoritative idempotent release
  seam (output may be `None`). Durable = the `ExecutionLeaseRef` JSON under
  `context.metadata` (persisted via `RuntimeState(metadata=...)` in `journal.py`),
  re-attached on resume — never the live object.
- **C (routing):** ROUTED today: read/write/edit/patch. UNROUTED (direct disk):
  `glob_search`, `grep_search`, `notebook_edit`, `artifact_list/read/preview`,
  and the universal local `Path.exists/is_file/resolve` in
  `filesystem/_paths.py`. `ExecutionBackend` needs list/glob/grep/stat/delete
  methods; partial support must be visible via capabilities (no local fallback
  after a remote lease).
- **D (artifacts):** reconcile execution `ArtifactRef` (has `digest`) with the
  context `ContextArtifactRef` spill vocabulary at `executor/spill.py` /
  `executor/allowed.py:303-330`.
- **E (subagent policy + cleanup):** child re-enters via `ChildRunner` taking
  only `AgentRunInput`; a lease inherit/share/isolate policy travels as a
  metadata token or widens the callable; cleanup co-locates with
  `cleanup_child_workspace`. Cover all ~10 exit paths idempotently.
