# A2A adapter — expose an agent to other agents (Agent2Agent)

The A2A adapter exposes an agent-driver agent over the
[Agent2Agent protocol](https://a2a-protocol.org/) so **other agents** can
discover and call it. Like MCP, it is hand-rolled JSON-RPC 2.0 (no `a2a` SDK
dependency) served on the same ASGI stack as the OpenAI server.

This is Phase 4 of the [platform-adapters plan](archive/2026-06/platform-adapters-plan-2026-06-10.md);
it reuses the Phase-2 ASGI stack and bearer auth.

## Install

The HTTP transport needs the `[server]` extra (Starlette + uvicorn):

```bash
pip install 'agent-driver[server]'
```

The A2A JSON-RPC core (`agent_driver.adapters.a2a.A2aServer`) is dependency-free;
only `agent_driver.adapters.a2a.http` needs Starlette, imported lazily.

## Run

Mount it on the server next to the OpenAI / MCP surfaces:

```bash
agent-driver serve --a2a --provider openrouter --model <model> --port 8000
```

This exposes:

- `GET /.well-known/agent-card.json` — the **Agent Card** (discovery: name,
  description, url, version, capabilities, skills). Public (no auth).
- `POST /a2a` — the JSON-RPC endpoint, gated by the same `--api-key-server` /
  `$AGENT_DRIVER_SERVER_API_KEY` bearer key.

## Methods (`POST /a2a`, JSON-RPC 2.0)

| Method | Behavior |
| --- | --- |
| `message/send` | Run the message as a task; returns a completed `Task` (the answer as the status message + an artifact). |
| `message/stream` | SSE: a `working` task snapshot, then a terminal `status-update` event (`final: true`). |
| `tasks/get` | Return a stored task by id (`-32001` if unknown). |
| `tasks/cancel` | Mark a task canceled. |

Uses the canonical JSON-RPC/HTTP shapes: lowercase task states
(`submitted`/`working`/`completed`/`failed`/`canceled`) and `role` (`user` /
`agent`) + `kind` discriminators on messages/parts/tasks — not the gRPC/proto
enum variant.

## Embedding directly

```python
from agent_driver.adapters.a2a.http import create_a2a_app, build_a2a_routes

app = create_a2a_app(agent, name="my-agent", url="https://host/a2a", api_key="secret")
# or mount the Agent Card + JSON-RPC routes into an existing Starlette app:
routes = build_a2a_routes(agent, name="my-agent", api_key="secret")
```

`create_app(agent, enable_a2a=True)` (the OpenAI server factory) does the latter.

## Not yet implemented / simplifications

- `message/send` runs synchronously and returns a completed task (no long-lived
  task queue); for long-running + HITL over HTTP use `/v1/runs` instead.
- No incremental artifact streaming (the stream emits a working snapshot + a
  terminal status-update), push notifications, or multi-skill cards.
- Text parts only (no file/data parts); single generic `chat` skill.
