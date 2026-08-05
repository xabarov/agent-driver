# EPIC-01 — Public ExecutionBackend Contract

Status: ready. Predecessor: current `main` and a green focused runtime/tool
baseline.

## Outcome

Agent Driver has one supported, backend-neutral execution seam that can replace
the current internal command/file callbacks without changing the agent loop or
governance order. A local compatibility backend proves the seam through the
real runner and keeps default behavior unchanged.

## Why this epic exists

`AsyncCommandRunner`, `AsyncFileIO`, local subprocess execution, and the
synchronous `FileBackend` solve adjacent problems but do not provide one public
contract, validated receipts, stable errors, or a lifecycle. External adapters
currently need internal imports and custom context management.

## Baseline to inspect

- `agent_driver/tools/context.py`
- `agent_driver/tools/builtin/shell.py`
- `agent_driver/tools/builtin/filesystem/`
- `agent_driver/fs/`
- `agent_driver/tools/executor/`
- `agent_driver/runtime/runner.py`
- `agent_driver/runtime/single_agent/types.py`
- `agent_driver/sdk/factory.py`
- `agent_driver/adapters/acp/server.py`
- `agent_driver/contracts/tools.py`
- `agent_driver/embedding.py`
- public export/schema snapshot tests

## In scope

1. Add a small public `agent_driver.execution` facade and validated contract
   models for backend identity, request identity, command result, file result,
   bounds, artifact references, and typed failures.
2. Define an async `ExecutionBackend` protocol. Keep the first implementable
   surface small, but reserve the identity and capability vocabulary required
   by the target contract.
3. Add a supported host injection seam. The expected starting point is a typed
   `RunnerConfig` dependency or a typed resolver owned by the host; do not place
   backend selection in model tool arguments or free-form metadata.
4. Add a local compatibility implementation that preserves existing shell
   policy, cwd/jail behavior, timeout result shape, output bounds, and file
   behavior.
5. Route at least the built-in `bash`, `read`, and `write` paths through the new
   seam when configured. Leave unchanged behavior when it is not configured.
6. Provide compatibility shims for ACP's current `AsyncCommandRunner` and
   `AsyncFileIO`, or migrate ACP to the new seam without an internal import.
7. Add a minimal deterministic fake backend for tests. The reusable external
   compliance kit is EPIC-05.
8. Publish the embedding-essential protocol/contracts through supported
   facades and document their stability.

## Non-goals

- Provisioning containers or remote workers.
- Reusing an environment across runs.
- Full remote `ls`/`glob`/`grep`/delete/edit behavior; EPIC-03 closes complete
  workspace routing.
- Streaming command output, reconnect, signals, or hard teardown; EPIC-04 owns
  those semantics.
- Inferring installed programs from local PATH.

## Design constraints

- Do not expose a raw `dict[str, Any]` as the new public result contract.
- Do not make a concrete container SDK a core dependency.
- Do not bypass `GovernedToolExecutor`. Dispatch remains inside the permitted
  tool handler path.
- Do not rename or reinterpret `workspace_id`, `workspace_cwd`,
  `BackgroundRunLease`, or `HarnessAdapter`.
- The backend receives stable run, attempt, tool-call, and request identity.
- Default local execution must remain compatible even if the host never opts in.
- Public failures must be bounded, redaction-safe, and categorizable without
  parsing messages.

## Work packages

### A. Contract decision and inventory

Map every current command/file call path, context scope, public export, and ACP
use. Record the selected module layout, injection point, compatibility strategy,
and initial method set in `TARGET_CONTRACT.md` before publishing code.

### B. Public contracts and protocol

Implement validated types and protocol with clear versioning and unsupported
semantics. Add round-trip, validation, schema, typing, and export tests.

### C. Local compatibility backend

Wrap current local execution rather than reimplementing policy. Prove matching
cwd, timeout, exit status, stdout/stderr, path jail, and error behavior.

### D. Runtime wiring

Create and clear backend run context on all terminal/exception paths. Propagate
identity through governed execution and tool traces without leaking backend
configuration to the model.

### E. ACP migration

Preserve editor-buffer and terminal routing. Remove ACP dependence on private
execution hooks only when behavior and focused ACP tests are green.

## Acceptance scenarios

1. With no configured backend, a real runner call using built-in `bash` behaves
   as before.
2. With the local compatibility backend, the same call returns the same exit,
   stdout/stderr, timeout, cwd, and trace semantics.
3. A fake backend receives correct run/attempt/tool-call/request identity only
   after governance allows the call.
4. A denied or approval-paused tool call never reaches the backend.
5. A configured backend routes `read` and `write`; local workspace data is not
   accidentally used for their bytes.
6. A backend exception becomes a typed, bounded, redaction-safe tool failure;
   the runner reaches a valid terminal or recovery path.
7. Concurrent runs do not leak backend context or cwd into each other.
8. ACP file and terminal routing remains green through its supported path.
9. Public imports, schemas, and JSON round trips are snapshot-tested.

## Definition of done

- All acceptance scenarios pass through the real runtime/tool pipeline.
- New public API and migration behavior are documented.
- Existing local and ACP focused suites pass.
- Full default tests and touched-module quality checks pass.
- SemVer impact and the EPIC-02 predecessor evidence are recorded.

