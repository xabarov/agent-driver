# ACP deepening plan — Tier 1 (editor-native I/O) → Tier 2 (rich updates) → Tier 3 (extra methods)

Status: planned (2026-06-10). Builds on the shipped ACP adapter
([`docs/acp.md`](acp.md), `agent_driver/adapters/acp/`) which covers
`initialize`/`authenticate`/`new_session`/`load_session`/`resume_session`/
`set_session_mode`/`prompt`/`cancel` + the `request_permission` round-trip.

The Agent Client Protocol is **bidirectional**: the editor drives the agent, but
the agent can also call back into the editor to read/write files, run terminal
commands, and report a live plan. We currently use only the agent-side methods
and `session/update` + `request_permission` callbacks. The biggest unrealized
value is the **client-side** callbacks — they are what make an agent feel native
in Zed (edits show as unsaved buffers; commands run in the editor's terminal).

Authoritative surface (from the `acp` package, which encodes the spec):

- **Client methods the agent may call** (`acp.Client`): `read_text_file`,
  `write_text_file`, `create_terminal`, `terminal_output`,
  `wait_for_terminal_exit`, `kill_terminal`, `release_terminal`,
  `request_permission`, `session_update`, `elicitation/*`. We use only
  `request_permission` + `session_update`.
- **`session/update` variants**: `user_message_chunk`, `agent_message_chunk`,
  `agent_thought_chunk`, `tool_call`, `tool_call_update`, `plan`,
  `available_commands_update`, `current_mode_update`. We emit message text +
  tool_call/tool_call_update only.
- **Client capabilities** (in `initialize`'s `client_capabilities`): `fs`
  (`readTextFile`/`writeTextFile`), `terminal`, `elicitation`, `nes`. The agent
  MUST check these before calling the corresponding client method.

## Design principles

1. **Capability-gated.** Every client callback is used only when the client
   advertised the capability in `initialize`. Absent capability → fall back to
   today's behavior (local disk / local bash). Never assume.
2. **Thin adapter, reuse the runtime.** Prefer wiring the runtime's existing
   seams (the `FileBackend` protocol in `agent_driver/fs/`, the tool gate, the
   stream) over re-implementing file/exec logic in the adapter.
3. **Offline-testable.** Every new behavior is drivable by a fake ACP client
   that records the client-method calls, with no editor.

## Research trail & provenance (how we got here, 2026-06-10)

This plan is the output of a deliberate idea-mining pass. Recording where each
finding came from so future work can re-trace or challenge it.

### What we mined and what each source gave us

1. **The local `acp` package = the authoritative spec encoding.** We enumerated
   `acp.meta.AGENT_METHODS` and `CLIENT_METHODS` and introspected `acp.Client`.
   - This is the *primary* discovery: the **client-side methods** (`fs/*`,
     `terminal/*`, `elicitation/*`) the agent may call back on the editor, which
     our adapter does not use at all. Everything in Tier 1 traces to here.
   - Confirmed `acp.Client` already exposes `read_text_file` / `write_text_file`
     / `create_terminal` / `terminal_output` / `wait_for_terminal_exit` /
     `kill_terminal` / `release_terminal` — so the callbacks are callable today;
     only the runtime wiring is missing.
   - `InitializeRequest.client_capabilities` carries `fs` / `terminal` /
     `elicitation` / `nes` — the capability gating in Design principle #1.
2. **ACP spec site** (web) — [file-system](https://agentclientprotocol.com/protocol/file-system),
   [tool-calls](https://agentclientprotocol.com/protocol/tool-calls),
   [Zed ACP](https://zed.dev/acp). Gave the exact capability flags
   (`fs.readTextFile`/`fs.writeTextFile`), method params (`sessionId`/`path`/
   `line`/`limit`/`content`), the MUST-check-capability rule, and the full
   `session/update` union (→ Tier 2). The spec's framing of **bidirectionality**
   is the reasoning behind ranking Tier 1 highest.
3. **Neighbor projects** (`/home/user/_refs/`, read-only):
   - `hermes-agent` — the richest ACP neighbor: load/resume/**fork/list**,
     set_mode/**set_model**, **MCP-per-session**, **slash commands**, thought +
     usage updates. → Tier 2 slash commands, Tier 3 fork/list/set_model.
   - `deepagents` — **plan updates** (`write_todos` → `AgentPlanUpdate`) and
     **auto-approval** with command analysis. → Tier 2 `plan`.
   - `openclaw/acpx` [coverage roadmap](https://github.com/openclaw/acpx/blob/main/docs/2026-02-19-acp-coverage-roadmap.md)
     — independently lists **fs + terminal as the implemented core** and
     fork/list/resume as gaps; also flags "permission policies with path/arg
     rules" (finer than our tool-kind rules). Corroborates Tier 1 priority.
   - `hermes` [ACP server issue #569](https://github.com/NousResearch/hermes-agent/issues/569)
     — real-world driver for "run in Zed/JetBrains/Neovim".
4. **Anthropic** — [Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk)
   and Agent SDK docs. **Deliberately recorded as a null result for ACP:** ACP is
   a Zed standard, not an Anthropic protocol. Anthropic's posts reinforce depth
   in MCP / in-process MCP servers / subagents / sessions / HITL hooks — useful
   elsewhere, but they contribute **no** ACP-specific surface. Don't go looking
   to Anthropic for ACP features.

### Empirical findings from our own code (the feasibility crux)

- The builtin `read_file` tool reads **directly from disk**
  (`read_text_with_size_guard(path)`); the builtin write/edit tools likewise. They
  do **not** go through the `FileBackend` protocol that already exists in
  `agent_driver/fs/` (`StateBackend`/`LocalFilesystemBackend`/`CompositeBackend`).
  → This is *why* Tier 1a needs a backend-pluggable seam, not just a new backend.
- `planning_state` is internal run-context metadata and is **not** surfaced on
  `AgentRunOutput` (we checked: `out.metadata` has no usable todo list, the
  `todo_write` trace's `args_summary` is empty). → This is *why* Tier 2 `plan`
  is blocked on a small runtime projection rather than being a free win.
- The runtime's session store persists **assistant turns only**, not the user
  input turn (verified while building load_session replay) — context for why the
  adapter keeps its own transcript.

### Reasoning behind the tiering

- Tier 1 first because client fs/terminal is the only feature that changes the
  *experience* (edits in the editor, commands in its terminal) rather than adding
  protocol completeness; both neighbors and the spec point at it as the core.
- Tier 1a (fs) before 1b (terminal): smaller blast radius, and 1b reuses the
  same per-run backend-injection pattern 1a establishes.
- Tier 2 is cheap and independent, but `plan` carries a runtime prerequisite, so
  it is sequenced after the free `current_mode_update` / `available_commands`.
- Tier 3 is protocol completeness with no UX jump and (set_model) a missing
  runtime capability — deferred until there is demand.

---

## Tier 1 — Editor-native I/O (the marquee feature)

The single highest-value ACP capability. Two sub-parts; ship filesystem first.

### 1a. Client filesystem (`fs/read_text_file` / `fs/write_text_file`)

Route the agent's file read/write/edit tools through the editor so edits appear
as unsaved buffers and the editor tracks changes — gated on
`client_capabilities.fs.{readTextFile,writeTextFile}`.

**Feasibility note (the crux).** The builtin filesystem tools
(`agent_driver/tools/builtin/filesystem/{read,write,edit}.py`) currently do
**direct disk IO** (`read_text_with_size_guard(path)` / direct writes) — they do
*not* go through the `FileBackend` protocol in `agent_driver/fs/`. So routing
them to the client needs one of:

- **Approach A (chosen): backend-pluggable file tools.** Make the builtin
  read/write/edit tools resolve an optional `FileBackend` from the run context
  (e.g. `app_metadata["fs_backend"]`, or a contextvar set per run), defaulting to
  today's direct-disk behavior when absent. Add an `AcpClientBackend(FileBackend)`
  that implements `read`/`write`/`edit` by awaiting `conn.read_text_file` /
  `conn.write_text_file`. The ACP `prompt` injects this backend per run when the
  client advertised `fs`. Reusable beyond ACP and keeps the adapter thin.
- Approach B (rejected): adapter ships its own ACP-only file tools and overrides
  the agent's registry per session. More duplication, fights the fixed-at-
  construction tool registry.

**Wrinkle:** `FileBackend` is sync (`read(path) -> str`); the client calls are
async. Either make the backend methods async (touches the protocol + callers) or
bridge via a thread-confined run-loop handle. Decide during implementation;
prefer adding async variants to the backend protocol with sync fallbacks.

**Status: DONE (2026-06-10).** Implemented as a run-scoped **async** file-IO seam
rather than the sync `FileBackend` — the file tools are already async, so an
async seam sidesteps the sync/async wrinkle above: `tools/context.AsyncFileIO` +
`fs_io_scope`, consumed via `read_text_routed` / `write_text_routed` in the
builtin `read_file` / `file_write` / `file_edit` / `file_patch` tools (default =
direct disk). `adapters/acp/fs.AcpClientFileIO` proxies to
`conn.read_text_file` / `write_text_file` with per-op capability + disk fallback;
ACP `prompt` wraps the run in `fs_io_scope` when `initialize` saw the `fs`
capability. Path jail/validation still runs locally; only byte transfer is
redirected.

**Steps:**
- [x] `initialize`: capture `client_capabilities` on the server (fs).
- [x] `AcpClientFileIO` over `conn.read_text_file`/`write_text_file`.
- [x] Builtin file tools resolve a run-scoped `AsyncFileIO` (default disk).
- [x] ACP `prompt` injects it for the run when `fs` advertised.
- [ ] Offline test: fake client records `read_text_file`/`write_text_file` calls;
      a planned file edit routes through the client, not disk.

### 1b. Embedded terminal (`terminal/*`)

Run shell commands in the editor's terminal pane (live output, killable),
gated on `client_capabilities.terminal`. Emit a `tool_terminal_ref` on the
tool call so the editor shows the terminal inline.

**Feasibility note.** The `bash` tool runs a local subprocess. Like 1a, this
needs the bash tool to resolve a pluggable "command runner" from context, with an
`AcpTerminalRunner` that drives `conn.create_terminal` → `terminal_output` →
`wait_for_terminal_exit` → `release_terminal`. Larger than 1a; ship after it.

**Status: DONE (2026-06-10).** Same async per-run seam as 1a:
`tools/context.AsyncCommandRunner` + `command_runner_scope`; the `bash` tool's
`_execute_bash` routes through it when set (default = local subprocess). The
read-only command policy still runs first. `adapters/acp/terminal.AcpTerminalRunner`
drives `create_terminal` → `wait_for_terminal_exit` → `terminal_output` →
`release_terminal`; ACP `prompt` injects it when `initialize` saw the `terminal`
capability. `tool_terminal_ref` emission deferred (the tool timeline is rebuilt
from the trace post-leg, so correlating the live terminal_id is extra work).

- [x] `AcpTerminalRunner` over the terminal client methods.
- [x] Pluggable command runner seam for the `bash` tool (default = local).
- [x] ACP `prompt` injects it when `terminal` advertised.
- [ ] `tool_terminal_ref` on the tool call (deferred — see above).
- [x] Offline tests: lifecycle recorded; absent capability runs locally.

---

## Tier 2 — Richer `session/update` emission (incremental, cheap)

No client capability needed; pure additions to what the adapter pushes.

**Status: current_mode_update + available_commands DONE (2026-06-10).**
`mapping.current_mode_update` / `available_commands_update` / `slash_command_name`
/ `slash_help_text`; the server emits `current_mode_update` on `set_session_mode`,
`available_commands_update` on `new_session`/`load_session`, and handles `/clear`
+ `/help` in-band in `prompt` (no model call). `plan` + rich tool-call diff
content remain (below).

- [x] **`current_mode_update`**: emitted when `set_session_mode` changes the mode.
- [x] **`available_commands_update`**: advertise + handle `/clear`, `/help`.
- [x] **Rich tool-call content**: edit-family tools (`file_edit`/`file_write`/
      `file_patch`) emit an ACP *edit* tool call with a `diff` content block
      (old/new from the run's `tool_results` structured output) so editors render
      the change inline. (This unblocked once we found the structured tool data
      lives in `output.metadata["tool_results"]`, not on the bare trace.)
- [ ] **`plan`** (`update_plan`): map `todo_write` activity to `PlanEntry`s.
      *Blocked by data access* — structured todo items are not currently exposed
      on `AgentRunOutput` (planning_state is internal); needs a small runtime
      projection (e.g. surface the latest todo list on the output/metadata) before
      this is honest. Track that as a prerequisite.
- [ ] **Rich tool-call content**: map edit tools to `start_edit_tool_call`
      (old/new diff) and read tools to `start_read_tool_call` with locations,
      instead of the generic `start_tool_call`.

---

## Tier 3 — Extra agent methods (niche / larger, defer)

- [ ] `session/list` (paginated session inventory) + `session/fork` (branch a
      conversation) — need a session registry the adapter owns.
- [ ] `session/set_model` — only meaningful once the agent supports multi-model
      swapping (the adapter serves a single fixed model today).
- [ ] `session/close`, `elicitation/*` (structured form prompts), `document/*`
      (editor open/change/save lifecycle), `nes/*` (next-edit suggestions),
      `providers/*`. Pending demand.

---

## Sequencing

1. **Tier 1a (client filesystem)** — marquee; do first. ~1–2 sessions incl. the
   backend-pluggable file-tool seam.
2. **Tier 1b (embedded terminal)** — after 1a reuses the same per-run-injection
   pattern.
3. **Tier 2** — cheap wins; `current_mode_update` + `available_commands` are
   immediate, `plan` waits on the runtime todo projection.
4. **Tier 3** — as demand appears.

Each tier preserves the dependency-light core and the capability-gated, offline-
testable, thin-adapter principles above.
