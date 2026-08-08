# Planning And Control

Current planning work follows a simple rule: prefer prompt + model behavior +
small runtime guards before introducing complex orchestration.

## Planning Surfaces

There are two different planning concepts:

- Live progress planning: `todo_write` and `planning_state_update` keep a
  visible checklist and current step. This is progress telemetry, not approval.
- Modal approval planning: `enter_plan_mode` and `exit_plan_mode_v2` produce a
  plan artifact that can pause execution for user approval before risky work.
  `exit_plan_mode_v2` is the canonical public approval-exit tool name;
  `exit_plan_mode` is only a legacy trace alias.

Public chat presets use live progress planning. Approval planning is reserved
for implementation/dev/risky side-effect contexts.

## Runtime Reminders

Chat-mode prompt assembly injects compact runtime reminders when relevant:

- `planning_mode_active` - stay read-only and prepare an approval-ready plan;
- `planning_mode_sparse` - follow existing todos without restating the full
  checklist;
- `planning_mode_exit` - an approval plan has already been accepted (and, when no todos
  exist yet, nudges laying the approved plan out as a `todo_write` checklist — P6);
- `deliverable_request_active` - the user asked for the final draft/answer now.

These reminders are deliberately lightweight. They make the current mode hard
to forget without adding a separate DAG engine.

### Open-todos finalize gate (P1)

Beyond the soft reminders above, a run is not allowed to *quietly* finish with
planned work still open. When the model tries to emit a final answer while the
session plan has `pending`/`in_progress` todos, the tool-stage finalize step
re-prompts it (bounded, ≤3 times) with the concrete list of open items and an
instruction to finish them or cancel with `todo_write(merge=true)` — injected via
the universal protocol reminder path (not chat-mode gated), with an
`open_todos_finalize_blocked` signal. The bound means a model that insists on
finishing, or genuinely cannot complete the plan, is still allowed to finalize
rather than deadlock — a strong nudge, not a hard block. Inert with no plan.

## Clarification

`ask_user_question` is for genuinely blocking user-owned decisions. It is not
plan approval and should not be used to avoid producing a requested deliverable.

The current contract supports:

- old `prompt` + `choices` calls for compatibility;
- structured `questions` with 1-4 questions;
- short headers, optional previews, and 2-4 unique options per question;
- a freeform "Other" path in chat-demo through the clarification text box.

## Steering

Steering controls are represented as transport-neutral control requests and
queued commands. The runtime drains controls at step boundaries, emits lifecycle
events, and chat-demo can show/cancel queued steering messages.

Current useful controls include:

- enqueue a user message for the running assistant;
- switch model at the next boundary;
- cancel a queued command.

## Subagents

`agent_tool` can create native subagent spawn envelopes. Runtime converts them
into durable subagent group/run rows, supports sync and background execution,
and uses mailbox/notification helpers for parent-child communication.

Subagents are also covered by the force-planning boundary: risky child spawn or
side-effecting work should not bypass approval policy.
