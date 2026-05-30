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
scenarios. Public web presets expose web search/fetch plus live planning
progress. Filesystem/shell controls and raw approval planning are not part of
the public web surface.

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
feedback, web-search final answer, and subagent final answer.

## Design Backlog

If a live run reveals a product/UI problem that is not part of the current
runtime slice, record it in:

- `docs/chat-demo-design-improvement-plan-2026-05-29.md`
