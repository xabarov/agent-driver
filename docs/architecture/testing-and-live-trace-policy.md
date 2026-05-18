# Testing and Live Trace Policy

This document defines the mandatory verification policy for new tools and runtime features in `agent-driver`, including planning, context compaction/summarization, and profile behavior changes.

## Scope

Apply this policy when a change touches at least one of:

- `agent_driver/tools/**` (new tools, policy, executor, manifests, guardrails);
- `agent_driver/context/**` (planning, trimming, compaction, memory projections);
- `agent_driver/runtime/**` (step loop behavior, event semantics, checkpoint/replay behavior);
- `agent_driver/code_agent/**` (sandbox, tool surface, action loop semantics);
- adapter layers impacting provider/tool behavior in runtime (`agent_driver/llm/**`, `agent_driver/adapters/**`).

## Required Verification Lanes

Every qualifying change must pass all lanes below before merge.

### Lane 1: Fast deterministic tests (required)

- Run targeted tests for touched modules.
- Add or update tests for:
  - happy path behavior;
  - policy/guardrail edge cases;
  - regression for the specific bug/risk fixed.
- Validate deterministic replay/event expectations when runtime semantics change.

Minimum command pattern:

```bash
PYTHONPATH=. .venv/bin/pytest <targeted test files>
```

### Lane 2: Full offline suite (required)

Run full offline test suite to catch cross-area regressions.

```bash
PYTHONPATH=. .venv/bin/pytest
```

Expected result: no unexpected failures.  
If unrelated pre-existing failures exist, document them explicitly in the change notes and isolate your verification evidence.

### Lane 3: Live smoke with real provider (required for tool/runtime/context changes)

For tool/runtime/context changes, run at least one live smoke with a real provider (OpenAI-compatible/OpenRouter or equivalent configured provider).

Required checks:

- provider health/complete live smoke;
- at least one agent run that includes a governed tool stage.

Recommended commands:

```bash
AGENT_DRIVER_RUN_LIVE_TESTS=1 PYTHONPATH=. .venv/bin/pytest -m live tests/llm/test_live_providers.py
AGENT_DRIVER_RUN_LIVE_TESTS=1 PYTHONPATH=. .venv/bin/pytest -m live tests/runtime/test_live_agent_tool_smoke.py
```

Notes:

- `.env` in repo root is supported for live tests.
- Never print API keys/tokens in logs or notes.

### Lane 4: Deep trace review (required for non-trivial changes)

For non-trivial behavior changes (new tools, policy changes, planning/compaction changes, loop logic changes), inspect trace outputs, not only pass/fail status.

Review at least:

- event ordering and terminal reason;
- tool trace statuses and policy decisions (`allow|deny|interrupt`);
- interrupt/resume payload correctness (if applicable);
- context metadata consistency (planning state, observations, trim/compaction metadata);
- replay rendering sanity (`render_cli_replay(...)`) when loop behavior changed.

Minimum evidence expectation in PR/change notes:

- tested lanes executed (commands);
- key observed trace outcomes (2-6 bullets);
- explicit statement of residual risks (if any).

## Tool and Feature-Specific Expectations

### New or changed tools

- Contract tests: `ToolManifest`, args schema, output envelope shape.
- Executor tests: registered tool run, truncation budgets, denied/interrupt flows.
- Live smoke: agent run that executes the tool path (or deterministic mock payload for external search results) with governed executor.
- For side-effecting/system tools (filesystem write, shell): live lane must also assert
  tool trace status (`completed|denied|interrupt`) and key structured output fields
  relevant to safety (`exit_code`, `timed_out`, size/replacement counters).
- For notebook/filesystem edit tools, live lane must assert observable side-effect
  correctness (target file content changed as expected), not only envelope presence.
- For write/edit tools, include both create/overwrite and in-place replacement paths
  across live lanes, and assert counters (`replacements`, resulting mode/size fields)
  from structured output alongside on-disk state.
- For high-risk/medium-risk tools under approval thresholds, include live interrupt lanes
  that assert paused status, interrupt reason, approval payload tool identity, and
  denied trace row for the proposed call.
- For HITL-enabled tools, include live resume lanes (`approve` and at least one
  non-approve path like `reject` or `cancel`) and assert resulting terminal status
  plus whether side effects were applied or blocked.
- When `edit` is supported, include a live `ResumeAction.EDIT` lane asserting that
  execution uses edited args (observable side effect reflects edited payload, not
  the original proposed call).
- When `cancel` is supported, include a live `ResumeAction.CANCEL` lane asserting
  terminal status `cancelled` and no side effect application.

### Planning / TODO / state-update tools

- Verify planning state transitions in metadata.
- Verify planning events are emitted and replayable.
- Verify no duplicate or stale planning step after tool stage.

### Context trimming / compaction / summarization

- Validate invariants: no orphan tool observations, no broken action-observation ordering.
- Validate deterministic audit metadata fields.
- Validate summary output does not leak draft/internal analysis sections intended to be stripped.

### CodeAgent profile changes

- Verify loop semantics (`llm_call -> tool_stage -> llm_call`) remain deterministic.
- Verify side-effecting callable tools trigger approval only when actually invoked by code action.
- Verify offline eval-like cases for arithmetic/tool composition remain stable.

## Merge Gate

A change in scope is merge-ready only when:

- targeted tests pass;
- full offline suite passes;
- required live smoke passes;
- deep trace review findings are documented;
- no new lint/type errors are introduced in touched files.

If a gate is intentionally deferred, record:

- why it is deferred;
- risk impact;
- follow-up owner and due milestone.
