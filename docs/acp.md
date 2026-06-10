# ACP adapter — serve an agent to editors over the Agent Client Protocol

The ACP adapter exposes any agent-driver agent to [Agent Client Protocol](https://agentclientprotocol.com)
clients (Zed and other editors) over **stdio** — no HTTP server, no open ports,
no auth surface. It is a thin translator: it maps the runtime's streamed
answer, tool timeline, and approval interrupts onto ACP `session_update` /
`request_permission` messages, and maps the client's permission choices back
onto the runtime's resume actions. No business logic lives in the adapter.

This is Phase 1 of the [platform-adapters plan](platform-adapters-plan-2026-06-10.md).

## Install

The adapter needs one optional dependency, gated behind the `[acp]` extra:

```bash
pip install 'agent-driver[acp]'
```

The core import graph never pulls it in — only code that opts into ACP imports
`agent_driver.adapters.acp`.

## Run

```bash
agent-driver acp --provider openrouter --model <model> --permission-mode standard
```

`agent-driver acp` builds an agent from the same provider / tool / store /
permission options as `agent-driver chat`, then serves it over ACP on
stdin/stdout. Useful flags:

| Flag | Meaning |
| --- | --- |
| `--provider` / `--model` / `--base-url` / `--api-key` | Provider selection (same as `chat`). |
| `--permission-mode {yolo,standard,strict}` | Gate tool calls. `standard`/`strict` raise an approval interrupt that becomes an ACP `request_permission`; `yolo` (default) never asks. |
| `--tools` / `--tool` / `--tool-pack` | Which tools the agent may call. |
| `--acp-name` / `--acp-version` | Identity advertised to the client on `initialize`. |
| `--acp-unstable` | Negotiate the unstable ACP protocol variant. |
| `--store-kind` … | Persistence for checkpoints / event log (same as `chat`). |

The process speaks JSON-RPC on stdio and runs until the client disconnects
(stdin EOF).

### Zed configuration

Point Zed at the command in `settings.json` (`agent_servers`):

```json
{
  "agent_servers": {
    "agent-driver": {
      "command": "agent-driver",
      "args": ["acp", "--provider", "openrouter", "--model", "<model>", "--permission-mode", "standard"]
    }
  }
}
```

## Protocol mapping

| ACP method | Adapter behavior |
| --- | --- |
| `initialize` | Advertises `agent_info` (name/version) and capabilities (`image=false`, `audio=false`, `load_session=true`, session `resume`). No auth methods (stdio). |
| `authenticate` | No-op (no auth on stdio). |
| `new_session(cwd, …)` | Allocates a session bound to a fresh runtime thread; remembers `cwd` as the workspace; advertises the available permission `modes`. |
| `load_session(session_id, …)` | Re-registers the session and **replays its recorded transcript** (user + assistant turns) via `session_update` before returning. |
| `resume_session(session_id, …)` | Re-registers the session and continues it **without** replaying history. (Routed under the unstable protocol.) |
| `set_session_mode(session_id, mode_id)` | Switches the session's permission posture. Maps `default`/`yolo`/`standard`/`strict` to a per-run tool gate (see below). |
| `prompt(prompt, session_id)` | Runs one turn. Emits the answer as `update_agent_message_text`, the tool timeline as `start_tool_call` + `update_tool_call`, and bridges approval interrupts to `request_permission`. Records the turn into the session transcript. Returns a `PromptResponse` with the mapped stop reason. |
| `cancel(session_id)` | Flags the session and aborts the in-flight run; the turn returns `stop_reason="cancelled"`. |

### Session modes

`set_session_mode` maps an ACP mode id onto the runtime permission gate, applied
per run for that session:

| Mode | Behavior |
| --- | --- |
| `default` | Use the agent's construction-time gate (e.g. `--permission-mode`). |
| `yolo` | Allow every tool call without asking (overrides the default gate). |
| `standard` | Ask before dangerous tool calls. |
| `strict` | Ask before dangerous *and* cautious tool calls. |

`set_session_model` / `fork_session` / `list_sessions` are not implemented (the
adapter serves a single fixed model).

### Stop reasons

The terminal run status (and `terminal_reason`) maps onto ACP's stop reasons:

| Run outcome | ACP `stop_reason` |
| --- | --- |
| Completed normally | `end_turn` |
| Operator cancelled | `cancelled` |
| Step / budget / deadline limit | `max_turn_requests` |
| Approval rejected, policy/guardrail block, runtime/model error | `refusal` |

ACP has no error stop reason, so a rejected or failed run is surfaced as a
`refusal` rather than a misleading `end_turn`.

### Permission round-trip

When a run pauses on a tool-approval interrupt (e.g. under
`--permission-mode standard`), the adapter:

1. Builds a `ToolCallUpdate` + `PermissionOption`s from the interrupt's allowed
   actions (`approve` → `allow_once`, `reject` → `reject_once`).
2. Calls `conn.request_permission(...)` and waits for the client's choice.
3. Maps the chosen `option_id` back to a `ResumeAction` and resumes the run.
4. Continues streaming the resumed turn.

A reject ends the run as a `refusal`; an approve resumes it to completion. The
runtime does **not** re-ask an already-approved call, so an `allow_once`
outcome cannot loop.

## Embedding directly

To serve an agent you constructed yourself (bypassing the CLI):

```python
from agent_driver.adapters.acp import serve_acp

serve_acp(agent, name="my-agent", version="1.0.0")  # blocking, stdio
```

or `serve_acp_async(...)` inside an existing event loop. See
[`examples/cookbook/16_acp_adapter.py`](examples/cookbook/16_acp_adapter.py) for
an offline, in-process round-trip driven by a fake ACP client.

## Not yet implemented

- Live token-by-token streaming (the answer is emitted once per leg, not
  incrementally) and reasoning/thought deltas.
- `set_session_model` / `fork_session` / `list_sessions` (single fixed model).
- Plan updates (`todo_write` → `AgentPlanUpdate`) and image/audio prompt content.
- Image / audio prompt content blocks (text only).
