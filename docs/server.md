# OpenAI-compatible HTTP server

The server adapter exposes any agent-driver agent behind the OpenAI
`/v1/chat/completions` surface, so every existing chat client and SDK (OpenAI
SDK, LibreChat, Open WebUI, LangChain, …) can talk to it unchanged. It is a thin
translator: OpenAI request → `AgentRunInput`, and the runtime's answer / token
stream → a `chat.completion` object or `chat.completion.chunk` SSE frames. No
business logic lives in the adapter.

This is Phase 2 of the [platform-adapters plan](platform-adapters-plan-2026-06-10.md).

## Install

The server needs the `[server]` extra (Starlette + uvicorn):

```bash
pip install 'agent-driver[server]'
```

The core import graph never pulls them in — only code that opts into the HTTP
server imports `agent_driver.server`.

## Run

```bash
export AGENT_DRIVER_SERVER_API_KEY=my-secret
agent-driver serve --provider openrouter --model <model> --host 127.0.0.1 --port 8000
```

`agent-driver serve` builds an agent from the same provider / tool / store /
permission options as `agent-driver chat`, then serves it over HTTP. Useful
flags:

| Flag | Meaning |
| --- | --- |
| `--host` / `--port` | Bind address (default `127.0.0.1:8000`). |
| `--served-model-id` | Model id advertised at `/v1/models` and echoed in responses. |
| `--api-key-server` | Bearer key required from clients. Falls back to `$AGENT_DRIVER_SERVER_API_KEY`. |
| `--provider` / `--model` / `--base-url` / `--api-key` | Upstream provider selection (same as `chat`). |
| `--tools` / `--tool` / `--permission-mode` | Tool selection and gating (same as `chat`). |

Point any OpenAI client at it:

```python
from openai import OpenAI

client = OpenAI(base_url="http://127.0.0.1:8000/v1", api_key="my-secret")
resp = client.chat.completions.create(
    model="agent-driver",
    messages=[{"role": "user", "content": "Hello"}],
)
print(resp.choices[0].message.content)
```

## Endpoints

| Method / path | Behavior |
| --- | --- |
| `POST /v1/chat/completions` | `stream=false` → one `chat.completion` object with `usage`. `stream=true` → SSE of `chat.completion.chunk` frames, terminated by `data: [DONE]`. |
| `POST /v1/responses` | Responses API: `input` + `instructions`, stateful chaining via `previous_response_id`. `stream=true` → SSE of `response.*` events. |
| `GET /v1/responses/{id}` | Retrieve a stored response (`store: true`). |
| `DELETE /v1/responses/{id}` | Delete a stored response. |
| `POST /v1/runs` | Start an async run; returns `202` with the run object immediately. |
| `GET /v1/runs/{id}` | Poll run status (`queued`/`running`/`requires_action`/`completed`/`failed`/`cancelled`). |
| `GET /v1/runs/{id}/events` | SSE of lifecycle events, terminated by `data: [DONE]`. |
| `POST /v1/runs/{id}/approval` | Resolve a paused run's approval (`{"action": "approve"\|"reject"\|"cancel"\|"edit"\|"clarify"}`). |
| `POST /v1/runs/{id}/stop` | Cancel a run. |
| `GET /v1/models` | Lists the configured model id. |
| `GET /healthz` | Liveness probe. |

### Responses API (`/v1/responses`)

A stateful alternative to chat completions. The request carries a single
`input` (a string or a list of `{role, content}` items) plus optional
`instructions` (the system prompt); the response is a `response` object with
`output` / `output_text` + `usage`. When `store` is true (default) the
conversation is kept under the response `id`, so a follow-up request continues it
with `previous_response_id` (the server replays the prior turns) — no need for the
client to resend history. `GET` / `DELETE /v1/responses/{id}` retrieve and remove
stored responses; the store is a bounded LRU. `stream=true` emits
`response.created` → `response.output_text.delta` → `response.completed` SSE
events.

### Async runs (long-running + human-in-the-loop)

`/v1/chat/completions` is synchronous. For long-running work or
human-in-the-loop, `POST /v1/runs` starts a run in the background and returns
immediately; the run is then driven by polling `GET /v1/runs/{id}` and/or
consuming `GET /v1/runs/{id}/events` (SSE: `run.started`, `run.requires_action`,
`run.completed` / `run.failed` / `run.cancelled`).

When the run pauses on a tool-approval interrupt it reports
`status: "requires_action"` with a `required_action` object (interrupt id,
reason, title, allowed actions); the client resolves it with
`POST /v1/runs/{id}/approval` (mirroring the ACP `request_permission` round-trip
and the in-process gateway). `POST /v1/runs/{id}/stop` cancels a run. Runs are
held in a bounded in-memory map (oldest terminal runs evicted past the cap).

### Streaming frames

A streamed response is: a role chunk (`delta: {"role": "assistant"}`), one
content chunk per token delta (`delta: {"content": "…"}`), a terminal chunk with
`finish_reason`, then `data: [DONE]`. Concatenating the content deltas yields the
full answer.

Send `stream_options: {"include_usage": true}` to receive one extra chunk before
`[DONE]` carrying `usage` (with an empty `choices` array), per the OpenAI
contract. If the client disconnects mid-stream the run is **aborted** rather than
left running. A mid-stream failure is surfaced as a trailing `{"error": {...}}`
frame before `[DONE]`.

### Request parameters

`temperature` and `max_tokens` are passed through to every model call in the run
(`AgentRunInput.temperature` / `max_tokens` → `LlmRequest`); the runtime may
still reduce `max_tokens` on provider credit errors. `response_format`
(`{"type": "json_object"}` or `json_schema`) is passed through for JSON / schema
mode (provider support required).

### Errors

Failures use the OpenAI error envelope — `{"error": {"message", "type", "code"}}`
— with a meaningful status: `400` (bad request), `401` (auth), `429`/`5xx`
(upstream provider, mapped from the SDK provider exception), `504` (timeout),
`500` (a failed/incomplete run or internal error). A non-completed terminal run
is reported as an error, not an empty `200` completion.

### Tools and `finish_reason`

The agent **executes its tools internally** — a turn may run several tool calls
before producing its answer. From the client's perspective the response is the
final assistant text with `finish_reason: "stop"`. The server does **not** emit
OpenAI client-side `tool_calls` (which would ask the *client* to run the tool);
that surface does not map onto a self-driving agent.

## Authentication

A single bearer token, compared against `--api-key-server` (or
`$AGENT_DRIVER_SERVER_API_KEY`) in constant time. Requests to `/v1/*` without a
matching `Authorization: Bearer <key>` get `401`. When no key is configured the
server is **open** and logs a startup warning — only acceptable bound to
loopback.

## Session continuity

By default the server is **stateless**: the client resends the full
conversation in `messages[]` each request (standard OpenAI usage). Send an
`X-Session-Id` header to make it **stateful** — the server then accumulates the
conversation per session id, so a client can send only the new user turn and the
prior turns are replayed into the run. The header also becomes the runtime
`thread_id`.

## Embedding directly

```python
from agent_driver.server import create_app, serve_http

app = create_app(agent, model_id="my-agent", api_key="my-secret")  # ASGI app
# or block and serve:
serve_http(agent, host="127.0.0.1", port=8000, model_id="my-agent")
```

See [`examples/cookbook/17_openai_server.py`](examples/cookbook/17_openai_server.py)
for an offline, in-process round-trip driven by Starlette's `TestClient`.

## State: in-memory vs durable

The server's keyed state — chat sessions (`X-Session-Id`), stored responses
(`previous_response_id` chaining), and A2A tasks — lives in a pluggable
`RecordStore`:

- **Default (in-memory)**: a bounded LRU (default 1024 per kind; configurable
  via `create_app(max_sessions=...)`). Fast, but lost on restart, and not shared
  across worker processes.
- **Durable (SQLite)**: pass `agent-driver serve --persist <path.db>` (or
  `create_app(record_store=SqliteRecordStore(path=...))`). Sessions / responses
  / A2A tasks then survive a restart — a fresh process on the same DB file
  resumes them. One store backs all three, namespaced.

Async **runs** are intentionally *not* persisted: a run's in-flight background
task cannot survive a process restart, so durable long-running work belongs to
the runtime's checkpoint/event-log layer rather than this record store.

## Not yet implemented

- Incremental OpenAI `tool_calls` streaming (see above — agent tools run
  server-side).
- `top_p` / `stop` / `n` / `seed` / `logprobs` sampling parameters.
- Token/tool streaming inside `/v1/runs` events (lifecycle events only for now;
  the final answer is in `run.completed`).
- Responses API: tool/reasoning item types in `output` (assistant text only),
  and the full incremental `response.*` event taxonomy (created /
  output_text.delta / completed only).
- Per-request keepalive comment frames (proxies may idle-timeout very long runs).
- Non-streaming client-disconnect cancellation (streaming runs already abort).
