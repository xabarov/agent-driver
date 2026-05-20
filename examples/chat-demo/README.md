# Chat Demo (Stages 1–7)

Demo chat application on `agent-driver`: FastAPI backend + React UI with
streaming, tools, sessions, HITL resume, and run replay.

## Quick start (development)

```bash
cd examples/chat-demo
make install
make dev-full      # backend :8010 + vite :5173 (recommended)

# Or two terminals:
make dev           # backend on :8010 (not :8000 — often used by other services)
make dev-frontend  # vite on :5173
```

Open `http://localhost:5173`.

If port 8010 is busy: `make dev-full APP_PORT=8020`

## Single-port demo build

```bash
make build
cd backend && . .venv/bin/activate && uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` (UI + API on one port).

## Frontend proxy

- `/api/*` -> `http://127.0.0.1:8010` (override with `VITE_API_PROXY_TARGET` / `APP_PORT`)
- SSE (`POST /api/chat/messages`, `POST /api/chat/runs/{id}/resume`) as `text/event-stream`

## Features by stage

| Stage | Capability |
| ----- | ---------- |
| 1–2 | Backend meta + chat SSE; React chat with markdown streaming |
| 3 | Sessions sidebar, `/sessions/:id` routing, transcript restore |
| 4 | Tool preset per request (`off/safe/dev/all`), `ToolCallCard` in chat |
| 5 | HITL: `interrupt_requested` → `InterruptCard` → `POST /api/chat/runs/{id}/resume` |
| 6 | Replay: `GET /api/sessions/{id}/replay?run_id=`, theme toggle, mobile sidebar |
| 7 | Static SPA via FastAPI, acceptance checklist below |

## Sessions

- Store file: `CHAT_DEMO_SESSIONS_PATH` (default `./.agent-driver/sessions.json`)
- Routes: `/sessions/new`, `/sessions/<session_id>`, `/sessions/<session_id>/replay/<run_id>`
- Runtime event log: `AGENT_DRIVER_RUNTIME_STORE_KIND=sqlite` recommended for HITL/replay durability

## Environment

See [`.env.example`](.env.example). Key variables:

- `AGENT_DRIVER_PROVIDER` — `fake` (default), `openrouter`, `vllm`, `ollama`
- `CHAT_DEMO_TOOL_PRESET` — default tool surface when UI does not override
- `AGENT_DRIVER_RUNTIME_STORE_KIND` — `memory` (fast tests) or `sqlite` (HITL + replay)

## Smoke checks

```bash
curl http://127.0.0.1:8000/api/health
curl "http://127.0.0.1:8000/api/tools?preset=safe"
curl -N -X POST "http://127.0.0.1:8000/api/chat/messages" \
  -H "content-type: application/json" \
  -d '{"message":"hi","tool_preset":"safe"}'
```

## Tests

```bash
make test
make test-frontend
```

## Acceptance checklist (manual QA)

- [ ] `/` redirects to `/sessions/new`; sidebar lists sessions
- [ ] Send message → URL becomes `/sessions/<id>`; transcript persists on reload
- [ ] Tool preset chips change next run; tool cards appear with real provider + `dev` preset
- [ ] Interrupt card shows on approval-required tool; Approve resumes stream
- [ ] Replay link opens read-only run timeline
- [ ] Theme toggle switches light/dark; provider badge shows health
- [ ] `make test` and `make test-frontend` pass
- [ ] `make build` serves UI from backend on one port

## Plan

See [`PLAN.md`](PLAN.md) for the full roadmap.
