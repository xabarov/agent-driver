# Agent Driver Roadmap

This is the short current roadmap. Historical phase notes were removed during
docs cleanup; detailed active work now lives in focused plans and concept docs.

## Direction

`agent-driver` is moving toward a small, composable Python runtime for agentic
chat applications:

- durable single-agent execution with checkpoints, event logs, resume, replay,
  and clear runtime contracts;
- governed tool execution with manifests, policy, guardrails, interrupts, and
  bounded outputs;
- chat-friendly planning that separates live progress from approval planning;
- steerability through queued controls instead of ad hoc UI state;
- subagents with durable child rows, mailbox handoff, background scheduling,
  and final synthesis;
- product verification through the chat demo, Phoenix traces, and Playwright
  concept checks.

## Current Priorities

1. Keep public chat behavior coherent.
   Planning should help the user, not trap the model in repeated plans,
   clarification loops, or approval churn.

2. Improve the planning/control surface with simple mechanisms first.
   Prefer prompt guidance, structured contracts, and small runtime guards before
   adding heavy DAG/workflow machinery.

3. Close the OpenClaude/Hermes-inspired workstream.
   The active execution board is
   [OpenClaude improvement plan](openclaude-improvement-plan-2026-05-29.md).

4. Keep the chat demo as the integration gate.
   User-visible concepts should be checked in the real UI and, when behavior is
   confusing, inspected in Phoenix traces.

5. Keep docs short and current.
   Add concise pages for live concepts; avoid restoring old exploratory notes.

## Quality Bar

- Tests scale with risk: focused unit tests for narrow changes, broader runtime
  and frontend checks for shared/user-visible behavior.
- Every phase touching chat behavior should include a chat-demo check or an
  explicit note explaining why it was not needed.
- End-of-phase quality work should include real refactoring with `pylint` over
  touched runtime/domain modules, not broad warning suppression.
- If a live design issue is found outside the current slice, record it as a
  short dated note in [Chat demo](chat-demo.md).

## Current Docs Map

- [Runtime overview](runtime.md)
- [Planning and control](planning-and-control.md)
- [Built-in tools](builtin-tools.md)
- [Chat demo](chat-demo.md)
- [Testing](testing.md)
- [OpenClaude improvement plan](openclaude-improvement-plan-2026-05-29.md)
