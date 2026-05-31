# Agent Driver Roadmap

This is the short current roadmap. Historical phase notes live in
`docs/archive/`; detailed active work lives in focused plans and concept docs.

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

The cross-workstream execution order is tracked in
[Unified work plan](unified-work-plan-2026-05-31.md). Treat that page as the
active sequence for artifact-first Deep Research, eval harness work, storage
convergence and optional SDK gateway/tool-server productization.

1. Make long Deep Research artifact-first.
   Report drafts should live in session workspace files, while chat stays a
   concise progress/final surface.

2. Measure the rewrite-loop fix.
   Add deterministic scenarios and trace assertions before expanding the
   workflow or spending on live provider runs.

3. Keep provider/model debugging disciplined.
   Cheap model preflight comes before expensive acceptance gates; run IDs stay
   in docs only when they explain a current regression or acceptance result.

4. Finish infrastructure convergence deliberately.
   Storage backend ordering/serialization and eval result contracts are active
   infrastructure work, not old refactor leftovers.

5. Keep closed work closed.
   Skills, SDK P0/P1, context pressure, structural splits and old OpenClaude/
   Hermes plans should remain decision records unless fresh traces show a
   regression.

6. Keep docs short and current.
   Add concise pages for live concepts; avoid restoring old exploratory notes
   or active-looking checklists in reference docs.

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

- [Unified work plan](unified-work-plan-2026-05-31.md)
- [Runtime overview](runtime.md)
- [SDK](sdk.md)
- [Planning and control](planning-and-control.md)
- [Built-in tools](builtin-tools.md)
- [Chat demo](chat-demo.md)
- [Provider and model debugging](provider-model-debugging.md)
- [Research quality summary](research-quality-improvement-plan-2026-05-31.md)
- [Efficient Deep Research workspace architecture](efficient-deep-research-workspace-architecture-2026-05-31.md)
- [Runtime metadata inventory](runtime-metadata.md)
- [Testing](testing.md)
