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

The large cross-harness, platform-adapter, deep-research and refactor plans that
drove the last cycles are now shipped and archived (see
[June 2026 archive](archive/2026-06/README.md) and
[May 2026 archive](archive/2026-05/README.md)); the
[Unified work plan](unified-work-plan-2026-05-31.md) keeps the slim record of
what is left. The genuinely-open threads are narrow:

1. Deep Research hard-profile hardening.
   The phase gate ships soft/optional by design; hard claim auditing
   (`research/claims.jsonl`) and a real (non-mock) PDF extractor are scaffolded
   but not production. Gate behind a live chat-demo health check.

2. Live cost discipline for the eval harness.
   The deterministic artifact/rewrite-loop scenarios pass; the live
   GPT-5.5 cost-regression gate is operational work, not code. Keep the live
   ladder cheap-to-expensive and record run IDs only when they explain a current
   regression or acceptance result.

3. Deferred-by-choice (decide before building).
   N7 heavy platform adapters (Telegram/Slack + delivery routing) and the
   remaining ACP client methods (`tool_terminal_ref`, `session/set_model`,
   `elicitation/*`) wait on explicit demand + a scope/dependency decision.

4. Keep closed work closed.
   Skills, SDK P0/P1, context pressure, structural splits, the E1–E8 /
   review-cycle / platform-adapter / node-contract plans and old OpenClaude/
   Hermes plans are archived decision records — do not reopen them from stale
   checkboxes unless fresh traces show a regression.

5. Keep docs short and current.
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

Active plans / status:

- [Unified work plan](unified-work-plan-2026-05-31.md) — slim record of remaining
  work after the shipped cycles.
- [Efficient Deep Research workspace architecture](efficient-deep-research-workspace-architecture-2026-05-31.md)
- [Research quality summary](research-quality-improvement-plan-2026-05-31.md)
- [Provider and model debugging](provider-model-debugging.md)
- [Runtime metadata inventory](runtime-metadata.md)

Guides / reference:

- [Embedding agent-driver (public API surface)](embedding.md)
- [Extending agent-driver](extending.md)
- [Runtime overview](runtime.md)
- [SDK](sdk.md)
- [Planning and control](planning-and-control.md)
- [Built-in tools](builtin-tools.md)
- [Node contract](node-contract.md)
- [Chat demo](chat-demo.md)
- [Testing](testing.md)

Closed plans (decision history):

- [June 2026 archive](archive/2026-06/README.md) — cross-harness backlog
  (E1–E8 / gap analysis / review cycle 2 / SDK cycle 3 / testing), platform
  adapters, ACP deepening, node contract, tracing, refactor, python sandbox.
- [May 2026 archive](archive/2026-05/README.md) — earlier phase logs +
  Deep Research live hardening.
