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
