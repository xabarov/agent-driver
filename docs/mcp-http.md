# MCP Streamable-HTTP transport

agent-driver can expose an agent as an **MCP server** so external MCP clients
(Claude Code, Cursor, Codex, another agent) can drive it. The JSON-RPC core
(`AgentMcpServer`) is transport-agnostic; two transports ship:

- **stdio** — `serve_stdio(server)` (dependency-free, see
  [`examples/cookbook/07_mcp_server.py`](examples/cookbook/07_mcp_server.py)).
- **Streamable HTTP** — this doc. Serves the same core over HTTP on the Phase-2
  ASGI stack (the `[server]` extra: Starlette + uvicorn).

This is Phase 3 of the [platform-adapters plan](platform-adapters-plan-2026-06-10.md);
it reuses the existing MCP JSON-RPC core and the OpenAI server's bearer auth.

## Install

```bash
pip install 'agent-driver[server]'
```

`agent_driver.mcp_server` (the core + stdio transport) stays dependency-free;
only `agent_driver.mcp_server.http` needs the `[server]` extra, and it imports
Starlette lazily.

## Run

Mounted alongside the OpenAI surface on one server:

```bash
agent-driver serve --mcp --provider openrouter --model <model> --port 8000
```

This exposes `/mcp` (MCP) next to `/v1/chat/completions` and `/v1/models` on the
same process. The MCP endpoint is gated by the same `--api-key-server` /
`$AGENT_DRIVER_SERVER_API_KEY` bearer key.

## Endpoint behavior (`POST /mcp`)

The client POSTs JSON-RPC messages and reads the response as `application/json`:

| Request | Response |
| --- | --- |
| A request (`initialize`, `tools/list`, `tools/call`, `ping`) | `200` with the JSON-RPC response. `initialize` also returns a fresh `Mcp-Session-Id` header. |
| A notification (no `id`, e.g. `notifications/initialized`) | `202 Accepted`, empty body. |
| A JSON-RPC batch (array) | `200` with an array of responses (or `202` if all were notifications). |
| Malformed JSON | `400` with a JSON-RPC parse error (`-32700`). |
| `GET /mcp` | `405` — this server does not push server-initiated SSE messages. |
| `DELETE /mcp` (with `Mcp-Session-Id`) | `204` — terminates the session. |

### Tools exposed

`tools/list` advertises the agent surface:

- `agent_query` — run a one-shot query, return the answer.
- `session_send` — send one turn to a named session.
- `session_history` — return a session's persisted turns.

## Embedding directly

```python
from agent_driver.mcp_server.http import create_mcp_app, build_mcp_routes

# Standalone MCP-only app:
app = create_mcp_app(agent, server_name="my-agent", api_key="secret")

# Or mount /mcp into an existing Starlette app's routes:
routes = build_mcp_routes(agent, api_key="secret")
```

`create_app(agent, enable_mcp=True)` (the OpenAI server factory) does the latter
for you.

See [`examples/cookbook/18_mcp_http_server.py`](examples/cookbook/18_mcp_http_server.py)
for an offline round-trip driven by Starlette's `TestClient`.

## Not yet implemented / simplifications

- No server-initiated SSE stream (`GET /mcp` → 405); the server is
  request/response only.
- Session ids are minted and accepted but not strictly required on subsequent
  requests (lenient for simple clients).
- `protocolVersion` echoes the server's fixed value rather than negotiating the
  client's; the JSON-RPC surface is unaffected.
