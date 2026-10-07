# Chat Demo

The chat demo in `examples/chat-demo` is the main product integration surface
for current runtime concepts. Use it to verify behavior, not just UI styling.

## Dev Stack

Useful files:

- `examples/chat-demo/docker-compose.yml` - base stack.
- `examples/chat-demo/docker-compose.dev.yml` - hot-reload dev stack.
- `examples/chat-demo/backend` - FastAPI backend and SSE relay.
- `examples/chat-demo/frontend` - React/Vite frontend.
- repo `.env` - provider keys and local runtime settings.

Typical dev URLs:

- frontend: `http://localhost:5174`
- backend: `http://localhost:8010`
- Phoenix: `http://localhost:6006`

## Provider Modes

The demo can run with a real provider from `.env` or deterministic fake
scenarios. Public presets expose web search/fetch, bounded agent delegation,
and live planning progress. Filesystem/shell controls and raw approval planning
are not part of the public web surface.

## Design Baseline

The chat demo UI follows a restrained "agent operations console" direction:
dense, quiet, highly legible, and focused on runtime inspection rather than
marketing-style presentation.

Current product/design rules:

- The public Tools UI exposes **Web Search**, **Web Fetch**, and **Agent
  Delegation**. Agent delegation uses the runtime's bounded `agent_tool`
  surface; local filesystem, shell, glob, grep, and raw planning tools are not
  user-facing web controls.
- New Deep Research and Skills UI must sit on top of shared `agent_driver`
  contracts. Chat-demo may add a Deep Research selector/button, progress
  cards, source/citation inspector, skill library/install UI and trust
  warnings, but reusable behavior stays in `agent_driver`: research contracts,
  source ledger, skill parsing, trust classification, invocation records,
  compaction survival, subagent preload and SDK/session APIs.
- The demo backend should remain a thin adapter from UI state to
  `AgentRunInput`, `ToolPolicyInput` or SDK/session calls. It must not grow a
  private `SKILL.md` parser, evidence ledger, final-readiness checker or
  Deep Research orchestrator.
- Planning is agent-controlled. The agent may use planning when a task needs
  it, while simple direct answers should stay direct. Planning outcomes,
  approvals, denials, and snapshots should be visible as runtime outcomes, not
  as manual tool handles.
- The header should keep provider health, selected model, current run context,
  and token metadata compact. Token metadata appears only after assistant usage
  data exists.
- Assistant output must remain readable in light and dark themes. Markdown,
  code blocks, planning snapshots, tool cards, and policy-denied tool feedback
  are part of the regression surface.
- Mobile keeps the sidebar hidden by default, the header compact, and the
  composer pinned above safe-area padding. The sidebar has its own mobile close
  control because the open sidebar layer covers the page header.
- The dependency baseline is enough for the current chat UI: React, Tailwind
  v4, Radix primitives, lucide icons, typography, markdown, and syntax
  highlighting. Avoid broad UI frameworks and generic chat UI frameworks.
  Consider focused additions only for concrete pressure: `cmdk` for command
  palette search, `@tanstack/react-virtual` for large lists,
  `react-resizable-panels` for a split run inspector, or a toast library for
  durable copy/error feedback.

Design guardrails:

- Prefer local component refinements, component tests, and Playwright checks
  before adding visual dependencies.
- Keep icon-only controls accessible with clear labels/tooltips.
- Respect `prefers-reduced-motion` for transitions and streaming indicators.
- Treat the chat demo as the product integration gate for new runtime states.

## Phoenix Tracing

The dev compose includes Phoenix tracing for backend spans. The backend exports
to the `agent-driver-chat-demo` project through the OTLP HTTP endpoint. Use it
when a live chat behaves oddly and screenshots are not enough to explain the
model/tool sequence.

## Concept Checks

Run deterministic browser smoke checks against a running frontend:

```bash
make test-chat-concepts CHAT_DEMO_URL=http://localhost:5174
```

Single scenario:

```bash
.venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py \
  --scenario clarification
```

Current concept scenarios cover clarification, plan approval, denied tool
feedback, simple direct answers, web-search final answer, ask-question denial
on deliverable turns, and subagent final answer.

Recommended orthogonal subset while developing planning/control behavior:

```bash
.venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_concepts_smoke.py \
  --scenario simple-direct \
  --scenario web-search-final \
  --scenario clarification \
  --scenario ask-question-denied \
  --scenario plan-approval \
  --scenario subagent-final
```

For live-provider debugging, run the matching user prompts in the real chat,
then inspect Phoenix at `http://localhost:6006`. Compare the trace shape against
the deterministic scenario: direct answers should not create tools, deliverable
turns should not pause on clarification, and subagent runs should end with a
coordinator synthesis rather than worker-only progress.

## JEV rollout check

The JEV control plane is enabled in the demo only when its gates and rollout
policy are injected into `RunnerConfig`; the default policy remains `off`.
For the normal chat task class, configure `CHAT_DEMO_JEV_ROLLOUT_MODE=active`
and optionally set `CHAT_DEMO_JEV_TASK_ALLOWLIST=normal_chat`; route roles resolve
through `AGENT_DRIVER_FAST_MODEL`, `AGENT_DRIVER_BALANCED_MODEL`, and
`AGENT_DRIVER_STRONG_MODEL`. Keep the allowlist explicit for the first rollout.
Run the cross-surface runtime validation before enabling a low-risk task class:

```bash
python -m agent_driver.evals.jev_live_validation --live --repeats 2 \
  --output artifacts/jev-stage5/live-validation
```

Then run the deterministic chat concepts and inspect the same routes in the live
demo. The rollout is acceptable for selected low-risk tasks when the validation
report is `passed: true`, shadow behavior is unchanged, the tool-policy and
memory invariants are true, and Phoenix shows the expected route/quality events.
Keep global `default_on` as an explicit operator choice; the selected
`normal_chat` allowlist can be promoted independently.
Use `CHAT_DEMO_JEV_GATE_MODES=routing=active,quality=active,compaction=shadow,memory=shadow`
to make the canary surface split explicit.

For a controlled canary, keep `normal_chat` in the task allowlist and run the
Stage 6 evaluator after the live validation artifact is captured:

```bash
CHAT_DEMO_JEV_ROLLOUT_MODE=active \
CHAT_DEMO_JEV_TASK_ALLOWLIST=normal_chat \
CHAT_DEMO_JEV_GATE_MODES=routing=active,quality=active,compaction=shadow,memory=shadow \
python -m agent_driver.evals.jev_live_validation --live --repeats 3 \
  --output artifacts/jev-stage6/live-validation
python -m agent_driver.evals.jev_stage6 \
  --live-validation artifacts/jev-stage6/live-validation/report.json \
  --stage5-report artifacts/jev-stage5/live-final/report.json \
  --chat-demo-check artifacts/jev-stage5/chat-demo-live-check.json \
  --output artifacts/jev-stage6/canary
```

The evaluator promotes only the configured active gates when all SLO and
invariant checks pass. The current canary is held by the 5-second p95 latency
limit in the initial unpooled run. The Stage 7 pooled transport calibration
meets that target; keep the demo on an explicit task allowlist until production
labels are collected.

When JEV is enabled, `/api/health` includes a bounded `jev` object with the
current rollout controller status and transport telemetry. The transport uses
one pooled HTTP client per cached agent bundle; `CHAT_DEMO_JEV_MAX_LATENCY_P95_MS`,
`CHAT_DEMO_JEV_MAX_FALLBACK_RATE`, and `CHAT_DEMO_JEV_MAX_COST_USD` control the
runtime rollback gates.

Reviewed production labels are stored as raw-free JSONL at
`CHAT_DEMO_JEV_LABEL_LEDGER` (default `.agent-driver/jev-production-labels.jsonl`).
The health payload reports only label count/windows; use the Stage 8 evaluator
to decide promotion.

Stage 9 active evidence for the two held gates is collected separately from
the demo ledger. Run the live memory validation and compaction benchmark, then
aggregate them with the reviewed routing/quality labels using
`python -m agent_driver.evals.jev_stage9`. The report uses a calibrated
compaction budget because its batched decision is slower and more expensive;
it still keeps global default-on disabled until an operator approves it and
independently reviewed production labels replace the canary evidence.

Live Phoenix-backed concept probe:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  .venv/bin/python examples/chat-demo/frontend/tests/e2e/chat_live_probe.py --all
```

This probe records screenshots, transcript excerpts, and trace summaries under
`/tmp/chat-demo-live`. The current suite checks direct chat, web research,
plan-only, deliverable-no-replan, clarification-only-when-blocked,
web-search-final, subagent synthesis, and steering at the next runtime
boundary.

For research-quality work, Phoenix inspection is part of the acceptance loop:
confirm the model searched, fetched concrete pages before synthesis, completed
visible todos, produced source links or source shelf evidence, and ended with a
terminal run event. If the browser looks acceptable but trace summary says
`repair_needed`, fix the shared runtime contract rather than special-casing the
demo.

## UI Smoke Checks

Run browser UI smoke checks against a running frontend:

```bash
CHAT_DEMO_URL=http://localhost:5174 \
  python3 examples/chat-demo/frontend/tests/e2e/chat_demo_smoke.py
```

The smoke covers empty state, sidebar search, model search, tools picker,
mobile sidebar open/close, keyboard reachability, and desktop/mobile/tablet/wide
layout invariants.

If a live run reveals a product/UI problem that is not part of the current
runtime slice, record it in this page under a short dated backlog note rather
than creating a long phase plan.
