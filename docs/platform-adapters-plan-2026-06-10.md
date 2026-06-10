# Platform adapters plan — Phase 1 (ACP) + Phase 2 (OpenAI-compatible HTTP/SSE)

Status: **Phases 1 (ACP), 2 (OpenAI HTTP/SSE) and 3 (MCP Streamable-HTTP)
implemented** (2026-06-10). Scope: expose the runtime to external clients over
standard protocols, keeping the core dependency-light and transport-agnostic.

Implementation notes:
- Phase 1 shipped in `agent_driver/adapters/acp/`, CLI `agent-driver acp`,
  `[acp]` extra, tests in `tests/adapters/test_acp_adapter.py`, docs
  [`docs/acp.md`](acp.md), example `examples/cookbook/16_acp_adapter.py`.
- Phase 2 shipped in `agent_driver/server/`, CLI `agent-driver serve`,
  `[server]` extra, tests in `tests/server/test_openai_server.py`, docs
  [`docs/server.md`](server.md), example `examples/cookbook/17_openai_server.py`.
- Runtime fix found during Phase 1: a dynamic `tool_gate` was re-consulted on a
  call the operator had already approved (`approved_interrupt_id` set), so a
  stateless gate looped approve→ask forever. Fixed in
  `agent_driver/tools/executor/governed.py` (skip the gate for approved calls,
  mirroring the static-policy short-circuit).
- Deviation from the Phase-2 sketch: the OpenAI surface does **not** emit
  client-side `tool_calls` — the agent runs its tools internally, so a turn
  returns the final assistant text with `finish_reason="stop"`. The
  `test_chat_completions_tool_calls` item was replaced by
  `test_internal_tool_use_returns_final_text`.
- Phase 3 shipped in `agent_driver/mcp_server/http.py` (Streamable-HTTP
  transport over the existing MCP JSON-RPC core), mounted on the Phase-2 ASGI
  app via `create_app(..., enable_mcp=True)` / `agent-driver serve --mcp`, tests
  in `tests/server/test_mcp_http.py`, docs [`docs/mcp-http.md`](mcp-http.md),
  example `examples/cookbook/18_mcp_http_server.py`. Request/response only
  (`GET /mcp` → 405; no server-initiated SSE). A2A / AG-UI / gRPC remain out of
  scope pending demand.

This plan covers the two highest-value, lowest-dependency network surfaces from
the cross-harness analysis:

- **Phase 1 — ACP adapter** (Agent Client Protocol, stdio): editor/IDE clients
  (Zed and others). Lowest new-dependency path; strongest neighbor consensus
  (both `hermes` and `deepagents` ship it via `agent-client-protocol`).
- **Phase 2 — OpenAI-compatible HTTP/SSE server**: maximal client reach (OpenAI
  SDK, LibreChat, Open WebUI, LangChain, any `/v1/chat/completions` client).

Deferred (Phase 3+): MCP Streamable-HTTP transport (reuse the existing JSON-RPC
core on the Phase-2 ASGI stack), A2A, AG-UI, gRPC. Rationale in
[`docs/...analysis`](#sources): immature client base (A2A), niche (AG-UI),
dependency-heavy and least universal in Python (gRPC).

## Design principles (non-negotiable)

1. **Core stays dependency-free.** Every transport lives behind an optional
   extra (`[acp]`, `[server]`), mirroring how `hermes` gates `[acp]`/`[web]`/`[mcp]`
   and how we already keep `chat-demo` FastAPI as example-only and the MCP
   server hand-rolled.
2. **Reuse existing building blocks** — do not re-implement streaming or the
   approval loop:
   - `agent_driver/gateway/gateway.py` — `AgentGateway.submit/respond/pending`,
     parking on `ACTION_REQUIRED`, resume via `ResumeAction`. Reference state
     machine for the permission/resume half.
   - `agent_driver/sdk/handle.py` — `RunStream.events()/text_deltas()` and
     `RunHandle` for token-level deltas + tool events.
   - `agent_driver/contracts/stream/events.py` — `RunStreamEvent`
     (`stream_id/run_id/seq/event/data`) projected from durable `RuntimeEvent`.
   - `agent_driver/adapters/sse.py` — native SSE framing (reused as-is for our
     own event stream; Phase 2 adds a *separate* OpenAI-chunk serializer).
3. **Adapters are thin translators.** They map our `RunStreamEvent` /
   interrupt / resume vocabulary onto the protocol's vocabulary and back. No
   business logic.
4. **Offline-testable.** Every adapter must be drivable by `FakeProvider` with
   no network, asserting the exact protocol message sequence.

## Current building blocks (inventory)

| Capability | Status | File |
| --- | --- | --- |
| MCP server (stdio JSON-RPC, hand-rolled, no `mcp` dep) | exists | `agent_driver/mcp_server/` |
| Native SSE framing (resumable via Last-Event-ID) | exists | `agent_driver/adapters/sse.py` |
| In-process approval gateway (submit/respond/pending) | exists | `agent_driver/gateway/` |
| Token/tool streaming primitives | exists | `agent_driver/sdk/handle.py` |
| HTTP server / WebSocket / gRPC / `serve` CLI | **absent** | — |
| ACP / OpenAI-compat / A2A / AG-UI adapters | **absent** | — |

---

## Phase 1 — ACP adapter (stdio)

### Why first

- **One optional dependency** (`agent-client-protocol`), transport is **stdio**
  → no HTTP server, no open ports, no auth surface, no TLS.
- Maps almost 1:1 onto the existing gateway approval loop and the
  `RunStreamEvent` token stream.
- Neighbor consensus: `hermes` (`acp_adapter/server.py`, `agent-client-protocol==0.9.0`)
  and `deepagents` (`libs/acp/deepagents_acp/server.py`, `agent-client-protocol>=0.9.0`).

### Dependency

```toml
[project.optional-dependencies]
acp = [
  "agent-client-protocol>=0.9.0",
]
```

Import lazily inside the adapter module so the core import graph never requires it.

### Module layout

```
agent_driver/adapters/acp/
  __init__.py        # exports AcpAgentServer, serve_acp
  server.py          # AcpAgentServer(acp.Agent) — protocol handlers
  session.py         # AcpSession: wraps a thread_id + RunStream + parked interrupt
  mapping.py         # RunStreamEvent -> session_update translators; ResumeAction map
  entry.py           # serve_acp(): build agent + acp.run_agent(...) over stdio
```

CLI: add an `acp` subcommand under `agent_driver/cli/commands/` (alongside
`ops.py`/`run_chat.py`) wired through `agent_driver.cli.main:main`, i.e.
`agent-driver acp` → `serve_acp(...)`. (Subcommand, not a new console script.)

### Gateway / runtime ↔ ACP mapping

| ACP agent-side method | Our implementation |
| --- | --- |
| `initialize` | Advertise `AgentCapabilities(load_session=True, prompt_capabilities=PromptCapabilities(image=False), session_capabilities=SessionCapabilities(...))`; `agent_info` = name/version; `auth_methods=[]` (no auth for stdio). |
| `authenticate` | No-op → return `AuthenticateResponse()`. |
| `new_session(cwd, mcp_servers)` | Allocate `thread_id`; store `cwd` as `app_metadata["workspace_cwd"]`; return `session_id` + advertised `models`/`modes`. |
| `prompt(prompt, session_id)` | Build `AgentRunInput` (text from ContentBlocks, `workspace_cwd` from session); drive `RunStream`; translate each `RunStreamEvent` to `conn.session_update(...)`; on interrupt → `request_permission`; return `PromptResponse(stop_reason=...)`. |
| `cancel(session_id)` | `RunHandle.abort("acp_cancel")`; return early with `stop_reason="cancelled"`. |
| `load_session` / `resume_session` (optional) | Replay persisted runtime events for the thread via `conn.session_update(...)` **before returning** (spec requirement). |
| `set_session_mode` / `set_session_model` / `fork_session` / `list_sessions` (optional) | Map mode→permission policy, model→provider/model swap; defer to a later iteration. |

### RunStreamEvent → ACP `session_update` translation (`mapping.py`)

| Our event (`RunStreamEvent.event`) | ACP update emitted |
| --- | --- |
| token-delta event (the one `RunStream.text_deltas()` consumes) | `acp.update_agent_message_text(text)` |
| reasoning/thinking delta (if present) | `acp.update_agent_thought_text(text)` |
| tool-call started | `acp.start_tool_call(tool_call_id, title, kind=<mapped>)` |
| tool-call completed/failed | `acp.update_tool_call(tool_call_id, status=..., content=...)` |
| todo/plan update (if `todo_write` fired) | `AgentPlanUpdate(entries=[PlanEntry(...)])` |
| run completed | return `PromptResponse(stop_reason="end_turn")` |

Tool-`kind` mapping (ACP kinds: read/edit/execute/fetch/search/other/think) is a
static dict keyed by our tool names (`read_file`→read, `file_write`/`file_edit`→edit,
`bash`/`python`→execute, `web_fetch`→fetch, `grep_search`/`glob_search`→search).

### Permission flow (the part that reuses the gateway state machine)

When a run pauses on an interrupt (the same `ACTION_REQUIRED` the gateway emits
with `interrupt_id/reason/title/description/allowed_actions/proposed_action`):

1. Build a `ToolCallUpdate` from the pending call + `PermissionOption`s derived
   from `allowed_actions` (map `ResumeAction.APPROVE`→`allow_once`/`allow_always`,
   `REJECT`→`reject_once`).
2. `outcome = await conn.request_permission(session_id, tool_call, options)`.
3. Map `outcome.option_id` back to a `ResumeAction` and resume the run (same
   resume path `AgentGateway.respond` uses: `interrupt_id` + `ResumeAction`).
4. Continue translating the resumed `RunStreamEvent` stream.

### Tests (offline, `FakeProvider`)

- `test_acp_minimal_prompt`: prompt → asserts ≥1 `update_agent_message_text`
  and `PromptResponse(stop_reason="end_turn")`.
- `test_acp_tool_call_updates`: a planned tool call → asserts `start_tool_call`
  then `update_tool_call(status=completed)` with the right `kind`.
- `test_acp_permission_roundtrip`: a run that pauses → asserts
  `request_permission` is called and an `allow_once` outcome resumes to completion.
- `test_acp_cancel`: `cancel()` mid-prompt → `stop_reason="cancelled"`, no further updates.
- `test_acp_capabilities`: `initialize()` advertises `load_session=True`.

Use a fake ACP `Client` (records `session_update`/`request_permission` calls) so
no editor is needed. Live smoke (manual, opt-in): connect from Zed.

### Phase 1 checklist

- [ ] `[acp]` extra + lazy import.
- [ ] `adapters/acp/{server,session,mapping,entry}.py`.
- [ ] `initialize/authenticate/new_session/prompt/cancel` (MVP set).
- [ ] RunStreamEvent→session_update translators incl. tool kind map.
- [ ] Permission round-trip via `request_permission` ↔ `ResumeAction`.
- [ ] `agent-driver acp` CLI subcommand.
- [ ] Offline test suite (5 tests above) + fake ACP client helper.
- [ ] `load_session`/`resume_session` history replay (can be a follow-up).
- [ ] Cookbook example `examples/cookbook/16_acp_adapter.py` (offline, fake client).
- [ ] Doc: `docs/acp.md` (run `agent-driver acp`, Zed config snippet).

### Risks / open questions

- The gateway emits batched terminal events; token-level ACP streaming must come
  from the **`RunStream` event stream**, not `gateway.submit`. Decision: the ACP
  session drives `RunStream` directly and reuses only the **parking/resume**
  semantics from the gateway (do not route token text through `gateway.submit`).
- ACP 0.9.0 may use `use_unstable_protocol=True` (hermes does). Pin behavior in a
  thin compat shim so a version bump is one edit.
- Reasoning/thought deltas only exist for providers that emit them — gate on
  presence.

### Estimated work: 1–2 focused sessions (~300–500 LoC + tests).

---

## Phase 2 — OpenAI-compatible HTTP/SSE server

### Why second

- **Maximal reach**: every existing chat client/SDK speaks
  `POST /v1/chat/completions`. Highest leverage surface in 2026.
- Bigger surface than ACP (auth + request/response translation + a real HTTP
  server), so it follows the cheaper ACP win.

### Dependency

Recommend **Starlette + uvicorn** (lighter than full FastAPI; we already depend
on `pydantic` and have our own `ContractModel`, so we don't need FastAPI routing):

```toml
[project.optional-dependencies]
server = [
  "starlette>=0.40.0",
  "uvicorn[standard]>=0.30.0",
]
```

(FastAPI is the alternative if request-body validation ergonomics are wanted; it
pulls Starlette anyway. `hermes` uses FastAPI/uvicorn on port 8642 as precedent.)

### Module layout

```
agent_driver/server/
  __init__.py        # exports create_app, serve_http
  app.py             # Starlette app factory: routes + auth middleware
  openai/
    schema.py        # request/response dataclasses for the OpenAI surface
    translate.py     # OpenAI messages <-> AgentRunInput; RunStreamEvent -> chunk
  auth.py            # bearer-token check (env-configured)
  entry.py           # serve_http(host, port, ...): uvicorn.run(create_app(...))
```

CLI: `agent-driver serve` subcommand → `serve_http(...)`.

### Endpoints (MVP)

| Method / path | Behavior |
| --- | --- |
| `POST /v1/chat/completions` | `stream=false` → one `chat.completion` object with `usage`; `stream=true` → SSE of `chat.completion.chunk` frames + `data: [DONE]`. |
| `GET /v1/models` | List the configured model id(s). |
| `GET /healthz` | Liveness. |

### Translation contract (`openai/translate.py`)

**Inbound** (`ChatCompletionRequest` → `AgentRunInput`):
- `messages[]`: `system` → system prompt; prior `user`/`assistant` turns →
  thread history (or flattened into `input` if no thread); last `user` → `input`.
- `model` → provider/model selection.
- `X-Session-Id` header (hermes uses `X-Hermes-Session-Id`) → `thread_id` for
  multi-turn continuity (reuse persisted thread state).
- `stream`, `temperature`, `max_tokens` → run config where supported.

**Outbound streaming** (`RunStreamEvent` → OpenAI `chunk`), sourced from
`RunStream` (NOT the native `adapters/sse.py` envelope — that is our own schema):
- token-delta → `{"choices":[{"delta":{"content": <text>}}]}`.
- tool-call started/args → `{"choices":[{"delta":{"tool_calls":[{...}]}}]}`.
- run completed → final chunk with `finish_reason` (`stop` / `tool_calls`) then
  `data: [DONE]`.
- 30s keepalive comment frames (`: keepalive`) like hermes, to survive proxies.

**Outbound non-streaming**: aggregate deltas into one `message` + `usage`
(prompt/completion/total tokens from `AgentRunOutput.usage`).

### Auth

Bearer token compared against an env var (e.g. `AGENT_DRIVER_SERVER_API_KEY`);
401 on mismatch. Disabled (open) only when the env var is unset **and** bound to
loopback, with a startup warning.

### Tests (offline, `FakeProvider`, Starlette `TestClient`)

- `test_chat_completions_nonstream`: shape of `chat.completion` incl. `usage`.
- `test_chat_completions_stream`: SSE frames are valid `chat.completion.chunk`,
  terminated by `[DONE]`; concatenated deltas equal the final answer.
- `test_chat_completions_tool_calls`: `tool_calls` delta + `finish_reason="tool_calls"`.
- `test_session_continuity`: same `X-Session-Id` across two requests reuses thread.
- `test_auth_required`: 401 without/with-wrong bearer; 200 with correct.
- `test_models_endpoint`: lists configured model.

### Phase 2 checklist

- [ ] `[server]` extra + lazy import (no core dep).
- [ ] `server/app.py` Starlette app + auth middleware.
- [ ] `openai/translate.py` inbound + outbound (stream + non-stream).
- [ ] `/v1/chat/completions`, `/v1/models`, `/healthz`.
- [ ] Session continuity via header → `thread_id`.
- [ ] `agent-driver serve` CLI subcommand.
- [ ] Offline test suite (6 tests above).
- [ ] Cookbook example `examples/cookbook/17_openai_server.py` (offline TestClient).
- [ ] Doc: `docs/server.md` (run `agent-driver serve`, point an OpenAI client at it).

### Risks / open questions

- OpenAI `tool_calls` streaming has a fiddly incremental-args format; start with
  whole-call emission, refine to incremental if a client needs it.
- Mapping multi-turn `messages[]` to our thread model: decide whether the server
  is stateless (client resends full history) or stateful (header thread). MVP:
  support both — stateless by default, stateful when `X-Session-Id` present.
- `usage` token accounting must come from `AgentRunOutput.usage`, not estimated.

### Estimated work: 2–3 sessions (server app + translation + auth + tests).

---

## Sequencing & estimates

1. **Phase 1 (ACP)** — ~1–2 sessions. Lowest deps, highest confidence, unlocks
   editors immediately.
2. **Phase 2 (OpenAI HTTP/SSE)** — ~2–3 sessions. Highest reach; introduces the
   `[server]` ASGI stack that Phase 3 can reuse.
3. **Phase 3 (deferred)** — MCP Streamable-HTTP on the same ASGI stack; small if
   done after Phase 2. A2A / AG-UI / gRPC remain out of scope pending demand.

Both phases preserve the dependency-light core: nothing new is required unless an
embedder opts into `agent-driver[acp]` or `agent-driver[server]`.

## Sources

- Cross-harness analysis (this repo conversation, 2026-06-09/10): current surface
  in `agent_driver/{mcp_server,adapters,gateway,sdk}`, and neighbor surfaces.
- `hermes-agent`: `acp_adapter/server.py` (ACP via `agent-client-protocol==0.9.0`),
  `gateway/platforms/api_server.py` (FastAPI/uvicorn OpenAI-compatible
  `/v1/chat/completions` + `/v1/responses`, SSE streaming).
- `deepagents`: `libs/acp/deepagents_acp/server.py` (ACP), serving via LangGraph
  platform (`langgraph-cli`/`langgraph-sdk`).
- `openclaude`: `src/grpc/server.ts` (gRPC bidi streaming) — informs why gRPC is
  deferred for a Python runtime.
- ACP agent-side contract checklist (subclass `acp.Agent`; `initialize`/
  `authenticate`/`new_session`/`prompt`/`cancel` minimal; `conn.session_update`
  for text/tool/plan; `conn.request_permission` for approvals).
