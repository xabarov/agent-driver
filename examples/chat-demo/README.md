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

## Real LLM + web search (not just "ok")

By default the backend uses the **Fake** provider (`AGENT_DRIVER_PROVIDER=fake`), which always replies **`ok`** — no reasoning, no tools.

1. Put credentials in the **repo root** `.env` (or `examples/chat-demo/.env`):

```bash
AGENT_DRIVER_PROVIDER=openrouter
AGENT_DRIVER_API_KEY=sk-or-...
AGENT_DRIVER_BASE_URL=https://openrouter.ai/api/v1
AGENT_DRIVER_MODEL=your/model
```

2. Restart `make dev-full` (backend reloads env from repo `.env` automatically).

3. In the UI set **Tools → Safe** (web + planning), **Workspace** (adds read-only session files), **Dev** (adds writes + governed shell), or **All**. **Off** disables all tools.

Header should show `openrouter · <model>` instead of `fake · default`.

## Agent workspace (file writes)

When **Tools** include filesystem write (`dev` / `all`), the agent writes under a **per-session sandbox**, not under `backend/`:

- Default root: `examples/chat-demo/workspace/<session_id>/`
- Override: `CHAT_DEMO_WORKSPACE_ROOT=/path/to/root` (absolute or relative to chat-demo root)
- Relative paths in `file_write` / `bash` resolve against that session folder
- `workspace/` is gitignored; reload the session page does not move files between chats

Remove old artifacts manually if they were created in `backend/` before this isolation (e.g. `snake_game/`).

## UI/UX (OpenRouter-like)

- Full-height layout: sidebar + sticky composer
- **Model picker** in header (`GET /api/models`, OpenRouter catalog when configured)
- **Tools** popover: preset toggle + live tool list from `GET /api/tools?preset=`
- Message avatars, copy assistant reply, tool cards with type icons
- **Runs** menu (replay) instead of chip row above chat
- Stream errors shown inline (SSL/network/API)
- Tools popover opens upward with opaque panel; tool list scroll + cap
- Assistant messages in subtle bubble; auto-scroll; session search by date groups
- `POST /api/chat/runs/{id}/cancel` for Stop (cooperative cancel + client abort)
- **Sessions sidebar:** `···` menu on each session → **Delete** → confirm dialog; active session redirects to new chat
- **Assistant markdown:** GFM links (styled, external open in new tab), lists/tables via Tailwind Typography
- **Code blocks:** fenced `python` / `json` / `bash` / `typescript` with highlight.js (github-dark theme)
- **Header tokens:** last assistant message shows `↑ prompt · ↓ completion` counts
- **Plan checklist:** live todo list inside the assistant bubble (`planning_snapshot` from SSE); progress bar, current step, completed items stay visible; raw `todo_write` cards hidden
- **Replay:** run replay shows the final plan state; reloading the session page does not restore plans (transcript is text-only)

## Single-port demo build

```bash
make build
cd backend && . .venv/bin/activate && uvicorn app.main:create_app --factory --host 127.0.0.1 --port 8000
```

Open `http://127.0.0.1:8000` (UI + API on one port).

## Frontend proxy

- `/api/*` -> `http://127.0.0.1:8010` (override with `VITE_API_PROXY_TARGET` / `APP_PORT`)
- SSE (`POST /api/chat/messages`, `POST /api/chat/runs/{id}/resume`) as `text/event-stream`

## Streaming And Retry Behavior

- Runtime streams emit durable token/progress events and still finish with a
  terminal event (`run_completed`, `run_failed`, or `run_cancelled`).
- `CHAT_DEMO_LLM_STREAM_IDLE_TIMEOUT_SECONDS` fails a provider stream that stops
  emitting events, so the UI does not stay in a permanent pending state.
- `CHAT_DEMO_SSE_KEEPALIVE_SECONDS` sends transport keepalives while the HTTP
  connection is quiet; this is separate from provider idle timeout.
- OpenAI-compatible provider streams tolerate empty `choices: []` chunks from
  gateways such as OpenRouter.
- Regenerating an assistant response truncates the persisted session transcript
  from the retried run before starting a replacement run.
- Browser reconnect for `POST /api/chat/messages` is idempotent when the
  frontend supplies `client_request_id`; duplicate starts replay/backfill the
  existing run instead of appending another transcript row.
- Chat message runs execute independently of the HTTP response that first
  created them, so a disconnected browser can reconnect and continue tailing the
  same durable event stream.
- Runtime streams emit assistant lifecycle snapshots. Failed partial output is
  tombstoned before `run_failed`, and only finalized assistant text is persisted
  to the session transcript.

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
- Tool presets: `safe` does not expose filesystem tools; `workspace`/`dev` operate only on the per-session workspace.
- Runtime event log: `AGENT_DRIVER_RUNTIME_STORE_KIND=sqlite` recommended for HITL/replay durability

## Environment

See [`.env.example`](.env.example). Key variables:

- `AGENT_DRIVER_PROVIDER` — `fake` (default), `openrouter`, `vllm`, `ollama`
- `CHAT_DEMO_TOOL_PRESET` — default tool surface when UI does not override
- `CHAT_DEMO_DEADLINE_SECONDS` — run wall-clock limit; default `600` for longer research/write tasks
- `CHAT_DEMO_LLM_STREAM_IDLE_TIMEOUT_SECONDS` — fail a provider stream that stops emitting events; default `60`
- `AGENT_DRIVER_RUNTIME_STORE_KIND` — `memory` (fast tests) or `sqlite` (HITL + replay)

## Smoke checks

```bash
curl http://127.0.0.1:8010/api/health
curl http://127.0.0.1:8010/api/models
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
