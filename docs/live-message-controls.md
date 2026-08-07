# Live-message controls

Agent Driver exposes one versioned, transport-neutral contract for changing an
active run without confusing conversation input with Stop or tool authority.
The public wire schema is `agent-driver.live-message-controls.v1` and the
persisted control schema version is `1`.

## Semantics

| Intent | Request | Runtime meaning |
| --- | --- | --- |
| Steer (new user turn) | `ENQUEUE_USER_MESSAGE` + `NOW` | Append a new user turn at the next safe active-turn boundary. Never abort the current LLM request or a tool. |
| Soft steer (fold, no new turn) | `STEER_USER_MESSAGE` | Fold the guidance into the CURRENT turn at the next safe boundary — appended to the last tool-result message so it rides the pending LLM call as guidance on the work in progress (alternation-safe, no abort tax, no redirect budget). Degrades to a new user turn when there is no tool message to fold into. |
| Hard redirect | `REDIRECT_USER_MESSAGE` + `NOW` | Advance the LLM generation, close only the local in-flight model await, append the correction as a real user message, and request a continuation. Any text the model had already streamed before the abort is preserved as an assistant checkpoint turn ahead of the correction (A4) — the partial draft is never silently discarded (text only; signed reasoning is not replayed). During a tool or approval phase it resolves as soft steer. |
| Separate next turn | `ENQUEUE_USER_MESSAGE` + `NEXT` | Remain pending until the source run is durably terminal, then cross the host-owned idempotent NEXT handoff. It is never drained by an active step boundary. |
| Cancel next | `cancel_next(queue_id)` | Cancel only a queued NEXT item before handoff. Applied or claimed items are not deleted. |
| Pause (resumable) | `PAUSE` | Park the run at the next safe boundary as a `MANUAL_PAUSE` interrupt (`pause_current` semantic). An in-flight LLM/tool call finishes first (no mid-flight abort); the run returns a `PAUSED` output. Resume by delivering a `ResumeCommand` with `ResumeAction.CONTINUE` (or `CANCEL` to end it). Unlike Stop, a pause is fully resumable and leaves no `run_stopped` failure. |
| Stop | `INTERRUPT` + `NOW`, or the host abort seam | Establish a durable preemption boundary. Pending messages become failed with `run_stopped`; no live message grants cancellation authority over a tool or socket. |

`LATER` and non-message control kinds remain legacy generic queue behavior and
are not part of live-message contract v1.

### Drain order (priority preemption)

When several controls are eligible at one step boundary they drain in a single
canonical order — `dispatch_order(item)` in `agent_driver.contracts.control`, the
sole source of truth shared by every command-queue store (in-memory / SQLite /
Postgres), so the order is identical across backends. Lower rank drains first:

1. `INTERRUPT` — a stop preempts everything; never do work behind it.
2. `REDIRECT_CURRENT` — mid-flight hard redirect.
3. `STEER_CURRENT` — soft steer folded into the current turn.
4. `QUEUE_NEXT` — deferred to the next turn (not eligible mid-run anyway).
5. everything else (config/state controls) by `NOW` → `NEXT` → `LATER`.

Ties within a rank break FIFO by enqueue sequence.

## Public surface

The contracts facade exports `LiveMessageSemantic`, `LiveMessagePhase`,
`LiveRunState`, `LiveMessageCapabilities`, `NextTurnHandoff`, and the typed
queue/request/response models. The runtime and aggregate embedding facades
export:

- `PostgresCommandQueueStore` and `PostgresControlStoreConfig`;
- `live_message_capabilities()` for a fail-closed capability read;
- `live_message_receipt()` for raw-message-free API/event projection;
- `live_message_transition_event()` for stable typed transition events;
- `dispatch_next_turn()` for the idempotent host handoff.

The SDK `Agent` exposes `steer`, `redirect`, `queue_next`, `cancel_next`,
`stop`, and `get_control`. The live methods reject an unknown or already
terminal run instead of silently falling back to a process-local queue.

## Receipt and event contract

Every accepted row has stable `queue_id`, `control_id`, FIFO `sequence`,
requested and resolved semantics, acceptance/application phases, `applies_at`,
timestamps, reason codes, content/request SHA-256, source/LLM generations,
claim identity, and optional NEXT handoff/destination identity. Idempotency is
scoped to the route, source, kind, and dedupe key; replay must match the full
semantic request, including metadata, or it fails with
`idempotency_conflict`.

Typed runtime transitions include `command_accepted`, `command_applied`,
`command_cancelled`, `command_failed`, `command_redirected`,
`command_promoted`, `command_stop_preempted`, and `next_handoff_completed`.
The allowlisted event projection contains hashes and state only. It never
contains message text. An authenticated host may separately return the
operator's own pending text from its product read model.

## Phase and race rules

- Admission and terminal commit serialize in the durable store. A request that
  observes terminal is rejected with `turn_no_longer_steerable`; a command
  accepted first but not yet applied is atomically promoted to NEXT with
  `terminal_promoted_to_next`.
- Stop admission sets the durable preemption boundary immediately. The runner
  may still observe and apply the Stop command at its cancellation seam, but no
  pending message is eligible after acceptance.
- A hard redirect advances `llm_generation` before local cancellation. Output
  from a lower generation raises `LlmGenerationSuperseded`, emits
  `result_fenced`, and cannot checkpoint, dispatch a tool, or commit terminal
  state.
- Claims are at most once. A host recovering a dead claimant must first fence
  that process and then either reuse its stable claimant identity or explicitly
  release the recorded claim; it must not invent a new command.

## NEXT host transaction

`dispatch_next_turn()` passes one `NextTurnHandoff` containing the stable
`handoff_id`, source identity, sequence, raw message, and content hash to a
host callback. That callback is the transaction boundary: it must idempotently
map `handoff_id` to one destination turn and append exactly one user message
with the same provenance before returning the destination ID. Agent Driver
marks the source row applied only after the callback succeeds.

A retry uses the same claimant and handoff IDs. If the process dies after host
commit but before generic readback, the callback returns the existing
destination and the source row is completed without creating another turn.

## Store support and limitations

Postgres is the production durability authority. Its mutations execute under a
schema-scoped advisory transaction lock, so admission, claims, generation
advance, terminal promotion, Stop, cancellation, and handoff completion are
cross-process atomic. In-memory and SQLite stores implement the same semantic
contract for tests and diagnostics; SQLite coordination is process-local.

Hard redirect proves local cancellation and generation fencing. It does not
claim that a remote provider cancelled work already received, and it never
cancels a running tool, child, job, or network socket.

## Recognized-but-unwired kinds

A drained control that the current execution context cannot act on is always
resolved (never left `QUEUED` to re-drain), and the outcome is reported honestly
so a host can distinguish three cases:

- `control_payload_invalid` — the kind is wired but the payload is malformed.
- `control_kind_not_implemented` — the kind is recognized but has no consumer in
  this context. Today this covers `STOP_SUBAGENT` / `CONTINUE_SUBAGENT` on the
  single-agent chat dispatcher, which holds only the command queue.
- `control_kind_unsupported` — the kind is unknown to this build (version skew).

All three mark the queued item `FAILED`.

### Two cancellation seams

`STOP_SUBAGENT` / `CONTINUE_SUBAGENT` would need one of the runtime's two
cancellation seams, neither of which the boundary dispatcher can currently reach:

1. **Run-level abort** — the `abort_handle` threaded into the drain (used by
   `INTERRUPT`), backed by the durable `AbortLifecycleStore`
   (`requested → observed → cancelled | completed_before_cancel`).
2. **Child/subagent abort** — the parent→child abort-handle chain in the subagent
   executor (`parent_abort_handle.child()` + `is_aborted` short-circuits), today
   reachable only from the tool stage (via the `task_stop` / `send_message`
   tools), not from the control dispatcher.

Wiring the subagent controls means bridging the dispatcher to seam 2 (a subagent
store / child-handle registry in drain scope) — a separate change, deliberately
not faked here.

See [live-message migration](live-message-migration.md) for additive rollout,
mixed-version rejection, legacy quarantine, and rollback.
