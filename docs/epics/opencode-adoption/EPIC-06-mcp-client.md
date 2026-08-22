# EPIC-06 — Real outward MCP client (M)

Status: **stdio slice DONE (2026-08-22); HTTP/SSE + OAuth deferred.** Track:
[opencode-adoption](README.md). Source idea: opencode ships a real outward MCP client
(stdio + streamable-HTTP + SSE, OAuth2+PKCE); ours was the single biggest concrete
capability gap — `tools/builtin/mcp.py` is a **readonly fixtures stub** that echoes static
descriptors and never connects to anything.

## What shipped: a real, dependency-free stdio MCP client

New package `agent_driver/tools/mcp_client/` (public via
`from agent_driver.tools.mcp_client import ...`):

- **`StdioMcpClient`** (`stdio_client.py`) — speaks JSON-RPC 2.0 over the MCP **stdio**
  transport (newline-delimited JSON on a subprocess's stdin/stdout). A background reader
  task demultiplexes responses to per-request futures by id; server notifications (no id)
  are ignored. Runs the `initialize` handshake (+ `notifications/initialized`), advertises
  **no** client capabilities (so a well-behaved server never asks it to sample/elicit),
  and implements `tools/list` (with `nextCursor` pagination), `tools/call`,
  `resources/list`, `resources/read`. Per-request timeouts; EOF / broken pipe fail all
  pending requests as `McpTransportError`; JSON-RPC errors map to `McpProtocolError`
  (with `code`); clean `aclose()` terminates the child. **No third-party MCP SDK and no
  network stack** — just `asyncio` subprocess pipes, so it runs and tests anywhere.
- **`register_stdio_mcp_server(registry, StdioServerConfig)`** (`registrar.py`) — connects,
  discovers tools, and registers each as a governed `ToolManifest` under a namespaced name
  `mcp__<server_id>__<tool>` whose handler proxies to `tools/call`. Discovered tools are
  `EXTERNAL_ACTION` / `MEDIUM` risk / `ON_POLICY_MATCH` approval by default (an untrusted
  third-party server never runs unattended under a permissive policy), carry
  `descriptor_provenance` metadata (`inventory_source: "mcp_stdio"`, `server_id`,
  `tool_name`), and honor `config.tool_allowlist`. `tools/call` output is flattened by
  `normalize_call_result` into `{summary, text, is_error, content, structured}` — a
  tool-level `isError` is surfaced distinctly from a transport/protocol failure. The
  caller owns the returned client's lifecycle.
- **`StdioServerConfig`** (`config.py`) — `server_id` (namespacing slug), `command`/`args`/
  `env`/`cwd`, `tool_allowlist`, timeouts, advertised client/protocol info. Carries **no**
  credential logic (the host resolves secrets into `env`).

Tests (`tests/tools/test_mcp_client.py`) drive the whole path against a **self-contained
fake MCP server** — a tiny Python program that speaks the real newline JSON-RPC framing:
handshake + two-page cursor-paginated `tools/list`, `tools/call` success + tool-error +
unknown-tool protocol error, discovery/registration with namespacing + provenance,
allowlist filtering, and bad-command transport error. Import-layering guard (EPIC-01),
export snapshot, and the full `tests/tools` sweep stay green. New subpackage is
auto-discovered by `packages.find` (no packaging change).

## Deferred (the rest of the M)

- **HTTP / streamable-HTTP / SSE transports.** Need an HTTP client (httpx or the Python
  `mcp` SDK, which is **not currently a dependency**) and a live server to test against —
  out of reach in this environment. `McpServerDescriptor.transport` already reserves
  `"http"`/`"sse"`. When added, keep the same `McpClient` protocol shape and registrar so
  only the transport swaps. Likely an **optional extra** (`agent-driver[mcp]`) so the core
  stays dependency-free.
- **OAuth2 + PKCE + dynamic registration.** Bearer/header auth first, interactive OAuth
  second; pairs with the deepseek-track credential-reference seam (see the survey). The
  legacy `mcp_auth` fixture tool documents the intended shape.
- **Config-driven server list + ACP `mcp_servers` wiring.** A `RunnerConfig`/SDK seam that
  connects a declared server list at agent build time and wires the currently-ignored ACP
  `mcp_servers` param. Deferred so the transport work lands first; hosts can already call
  `register_stdio_mcp_server` directly.
- **`tools/list_changed` live refresh.** Re-discover on the server's change notification
  (the reader loop already sees notifications; today it ignores them).

## Live-verified against the reference server

Verified end-to-end against the official `@modelcontextprotocol/server-everything`
(reference server, v2.0.0) over `npx`: handshake + `serverInfo`, paginated `tools/list`
(13 tools), `tools/call` (`echo`, `get-sum` → "42"), `resources/list` (7 resources), and
the full registrar path (governed namespaced registration + handler invocation). The live
run surfaced a real bug the fake-server tests missed: **real MCP servers use kebab-case /
dotted tool names** (`get-sum`, `get-tiny-image`), which are not valid Python identifiers
and were rejected by `ToolManifest`'s code-agent name check. Fixed by sanitizing the
*manifest* name to identifier characters (`mcp__everything__get_sum`) while preserving the
**raw** tool name for the actual `tools/call` (kept in `descriptor_provenance.tool_name`).
Captured by a default-sweep regression (a kebab tool in the fake server) plus a
`live`+`slow`-marked interop test (`tests/tools/test_mcp_client_live.py`, excluded from the
default sweep; run with `-m live`).

## Kept deliberately

The `tools/builtin/mcp.py` fixture stub stays registered by default (backward compat + an
offline demo/catalog path). The real client is opt-in via the new package; a future config
seam will make it the default outward path.
