# Chat Demo (Stages 1-2)

Bootstrap and backend MVP for a demo chat application built on top of
`agent-driver`.

## Quick start (backend)

```bash
cd examples/chat-demo
make install
make dev
```

Backend starts on `http://127.0.0.1:8000`.

## Frontend (Stage 2)

```bash
cd examples/chat-demo
make install
make dev           # terminal 1: backend on :8000
make dev-frontend  # terminal 2: vite on :5173
```

Open `http://localhost:5173`.

Frontend uses Vite proxy:

- `/api/*` -> `http://127.0.0.1:8000`
- SSE stream (`POST /api/chat/messages`) is passed through as `text/event-stream`

## Smoke checks

```bash
curl http://127.0.0.1:8000/api/health
```

```bash
curl -N -X POST "http://127.0.0.1:8000/api/chat/messages" \
  -H "content-type: application/json" \
  -d '{"message":"hi from curl"}'
```

## Tests

```bash
cd backend
pytest -q
```

```bash
cd ../frontend
npx pnpm test --run
```

## Plan

See `examples/chat-demo/PLAN.md` for the full roadmap.
