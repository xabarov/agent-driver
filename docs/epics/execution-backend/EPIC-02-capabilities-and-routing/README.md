# EPIC-02 — Capabilities and Safe Environment Routing

Status: blocked on EPIC-01.

## Outcome

Agent Driver knows, in a typed and truthful way, what the selected execution
environment can do. It exposes a bounded environment brief to the model and
withholds tools whose declared execution requirements are not satisfied. The
host remains the only authority that selects the backend and environment.

## Why this epic exists

A prepared environment is useful only if the agent can discover stable facts
about it without repeatedly installing, probing, or hallucinating tools. A
prompt listing programs is not sufficient: the executor needs the same
revisioned facts below the model, and absence of evidence must not turn into a
capability claim.

## Baseline to inspect

- EPIC-01 contracts and injection code
- `agent_driver/contracts/capabilities.py`
- `agent_driver/runtime/single_agent/types.py` (`CapabilitySettings`)
- `agent_driver/contracts/tools.py` (`ToolManifest`)
- `agent_driver/tools/registry.py`
- `agent_driver/runtime/single_agent/llm_step/prompt.py`
- request-only context handling in runtime and SDK
- capability pack and harness adapter contracts (reuse patterns, not meanings)
- redaction and contract snapshot tests

## In scope

1. Add a validated `ExecutionCapabilitySnapshot` bound to backend,
   environment revision, lease generation when available, and observation time.
2. Represent command, file, event, control, artifact, reconnect, timeout,
   output, resource, and teardown support with explicit status and reasons.
3. Add a bounded program/runtime inventory with names and versions only where
   verified. Do not serialize the entire environment or PATH.
4. Add typed execution requirements for tools, or an equivalent public mapping
   that is checked before model exposure and again before dispatch.
5. Derive a deterministic, redacted `EnvironmentBrief` for request-only model
   context. Include the capability revision and known limitations.
6. Define stale, degraded, and unknown behavior. A required stale/unknown
   capability fails closed for dispatch and produces an actionable host-facing
   reason.
7. Ensure backend/profile selection is host-derived, immutable for an active
   run attempt, and excluded from model tool schemas.
8. Emit safe capability-selection diagnostics and timings without secrets.

## Non-goals

- Installing or updating programs.
- Building images or choosing a concrete operating system.
- Registering every installed command as a model tool.
- Letting model output request a more privileged environment.
- Owning host network, secret, tenant, user, or scheduling policy.
- General replacement of existing `CapabilitySettings`; use a distinct name
  and bridge only where semantics genuinely overlap.

## Design constraints

- Snapshot facts are backend observations, not self-asserted model content.
- `supported`, `unsupported`, `degraded`, and `unknown` are distinct.
- Cache keys include backend/environment revision and lease generation where
  relevant; cache age/freshness is visible.
- Brief generation is deterministic, size-bounded, and redaction-tested.
- A tool may require capabilities but may not select the backend or environment.
- Installed capability and model-visible tool availability remain separate.
- A capability check supplements, never replaces, tool policy/approval.

## Work packages

### A. Snapshot contract

Define the minimum stable schema, validation, versioning, status vocabulary,
freshness, digest/revision, and redaction rules.

### B. Backend handshake and cache

Collect or retrieve the snapshot before tool-schema construction. Specify
refresh behavior and fail-safe handling for timeout, stale data, and drift.

### C. Tool requirement routing

Declare requirements independently of model arguments. Filter or mark tools
before the LLM call, then recheck the accepted snapshot immediately before
backend dispatch to prevent time-of-check/time-of-use drift.

### D. Environment brief

Project useful facts into request-only context with a strict character/token
budget and no raw backend payloads. Make unknowns visible instead of inventing
setup instructions.

### E. Diagnostics

Expose selected backend ID, snapshot revision/status, filtered tool names,
reason codes, cache/handshake timing, and drift without sensitive values.

## Acceptance scenarios

1. A verified capability snapshot makes a matching registered tool available
   and the brief names the capability revision.
2. An installed program with no registered/allowed tool does not appear as a
   callable model tool.
3. A registered tool with an unmet, degraded, stale, or unknown hard
   requirement is withheld or denied before dispatch with a typed reason.
4. Model text and tool arguments attempting to switch backend/profile have no
   effect.
5. Two concurrent runs with different snapshots receive different briefs and
   tool sets without context leakage.
6. Snapshot payloads containing secret-like keys/values are rejected or
   redacted before persistence, events, logs, and prompts.
7. Capability drift between schema construction and dispatch is detected and
   fenced or revalidated; the command does not run under a false assumption.
8. Snapshot acquisition timeout follows a documented fail-safe path and
   produces separate queue/handshake timing.
9. Default local runs without execution requirements behave as before.

## Definition of done

- Capability truth is enforced both above and below the model.
- The environment brief is useful, bounded, deterministic, and safe.
- Contract/export/schema, concurrency, drift, and real-run tests pass.
- Public docs explain how a host supplies capabilities without exposing
  credentials or environment internals.
- SemVer impact and EPIC-03 predecessor evidence are recorded.

