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
| `GET /v1/models` | Lists the configured model id. |
| `GET /healthz` | Liveness probe. |

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

## Bounded session memory

Stateful (`X-Session-Id`) conversations are held in a bounded LRU (default 1024
sessions; configurable via `create_app(max_sessions=...)`). The least-recently
used session is evicted past the cap, so a long-lived server cannot leak memory
on unbounded session ids.

## Not yet implemented

- Incremental OpenAI `tool_calls` streaming (see above — agent tools run
  server-side).
- `top_p` / `stop` / `n` / `seed` / `logprobs` sampling parameters.
- `GET /v1/responses` and other OpenAI endpoints beyond chat completions.
- Per-request keepalive comment frames (proxies may idle-timeout very long runs).
- Non-streaming client-disconnect cancellation (streaming runs already abort).
