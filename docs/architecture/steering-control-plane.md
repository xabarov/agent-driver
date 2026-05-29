# Steering Control Plane

This note defines the current `agent-driver` steering control plane. The goal is
to let hosts guide a live or resumable run without smuggling commands through
chat text, ad hoc metadata, or UI-only state.

## Contract

The public contract lives in `agent_driver.contracts.control`:

- `ControlRequest`: a host command routed by `run_id`, `thread_id`, or
  `agent_id`;
- `ControlResponse`: acceptance result with `control_id` and `queue_id`;
- `CommandQueueItem`: durable command queue row;
- `ControlKind`: command vocabulary;
- `ControlPriority`: `now`, `next`, `later`;
- `CommandQueueStatus`: `queued`, `applied`, `cancelled`, `failed`.

Payloads and metadata must stay JSON-serializable. Every request needs at least
one routing identifier so a host can enqueue before, during, or after a run
without relying on process-local object references.

## Queue Semantics

Command queues implement `agent_driver.runtime.control.CommandQueueStore`.

Ordering is deterministic:

- `now` before `next`;
- `next` before `later`;
- FIFO within each priority;
- dedupe keys return an existing pending command for the same route, kind, and
  source.

Current stores:

- `InMemoryCommandQueueStore` for tests and embedded hosts;
- `SqliteCommandQueueStore` for local durable queues and chat-demo dev mode.

Queued commands are independent from checkpoints. A command queued before a
runtime boundary survives runner recreation when the host uses a durable queue
store.

## Runtime Boundary

The single-agent runtime drains controls at the LLM step boundary. Today it
applies `now` and `next` items and leaves `later` queued for future scheduling
semantics.

Applied command kinds:

- `set_model`: stores `forced_model` in tool-policy metadata so the next LLM
  request uses the requested model;
- `enqueue_user_message`: appends a user message before the next LLM request;
- `set_permission_mode`: records the requested host permission mode in
  app metadata;
- `set_tool_policy`: replaces the run's `ToolPolicyInput`.

Unsupported or invalid payloads are not applied. Future work should mark them
`failed` with structured error details instead of silently leaving them queued.

## Events

Hosts should observe the control lifecycle through durable runtime events:

- `control_requested`;
- `command_queued`;
- `command_dequeued`;
- `control_applied`;
- `command_cancelled`.

`control_requested`, `command_queued`, and `command_cancelled` are emitted by
the SDK facade for `run_id`-scoped requests. `command_dequeued` and
`control_applied` are emitted by the runtime when a boundary drains a command.

SSE and replay are adapters over durable runtime events. Chat-demo uses this to
render queued steering chips during a live stream and a steering timeline in
run replay.

## SDK Surface

`agent_driver.sdk.Agent` exposes:

- `control(request)`;
- `enqueue(message, ...)`;
- `set_model(model, ...)`;
- `set_permission_mode(mode, ...)`;
- `cancel_queued_message(queue_id)`.

The facade accepts an optional `command_queue_store`. If omitted, it creates an
in-memory store. Production hosts should pass a durable store when commands must
survive process restart.

## Chat-Demo Adapter

Chat-demo exposes steering through:

- `POST /api/chat/runs/{run_id}/control`;
- `DELETE /api/chat/commands/{queue_id}`.

The frontend currently supports enqueueing a steering message while a run is
streaming, cancelling queued steering messages, and showing control lifecycle
events in replay. Model switching is still a Phase 4 follow-up.

## Design Rules

- Keep steering commands separate from normal transcript messages until the
  runtime deliberately applies them.
- Route commands by stable ids, not websocket connection state.
- Record every accepted/cancelled/applied command as a durable event.
- Treat UI state as a projection of the command queue and event log.
- Do not let child/subagent controls widen parent permissions.

## Remaining Work

- Add explicit failed-command events and tests.
- Persist higher-level steering summaries in session history if product UX
  needs them outside replay.
- Add chat-demo model-switch controls where the selected model can safely affect
  the next LLM boundary.
- Extend the same command plane to native subagent controls:
  `stop_subagent`, `continue_subagent`, mailbox notifications, and child plan
  approvals.
