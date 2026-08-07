# Design Decision — A3: steering pause / resume

Status: **proposed (awaiting sign-off).** Branch: `epic/steering-pause`. Part of the
steering epic (survey: `docs/backlog/agent-driver-harness-analysis/` + memory
`steering-theme`). Predecessors A1 (soft steer) + A2 (busy policy) merged.

## The gap

agent-driver can **stop** a live run (`INTERRUPT` → abort → terminal `CANCELLED`) and can
**pause** for a human decision (approval / `wait_for_event` → `PAUSED`, resumed by a
`ResumeCommand`). It cannot **hold a live run on demand and resume it** from the steering
plane — the openhands `pause()`/`PAUSED`/re-`run()` and hermes goal auto-pause primitive.
A host that wants "freeze while I think, then continue" has only Stop (destructive).

## Key finding: PAUSED already exists — it is interrupt-shaped

`RunStatus.PAUSED` + `RUN_PAUSED` event + `_build_paused_output` (finalization/output.py:600)
are built and working, but every path into them today is an **interrupt**: the paused
output is constructed around `result.interrupt` (an `InterruptRequest`), and resume
(`lifecycle/resume.py`) is written around `PendingInterruptState` (+ plan-approval
payloads). So the durable pause/resume machinery is there; A3 is **not** "add a pause
state" — it is "let the steering plane synthesize a *pause interrupt* and resume it as a
plain continue."

## Design: pause as a synthesized steering interrupt

1. **`ControlKind.PAUSE`** (new steering command) + `LiveMessageSemantic.PAUSE_CURRENT`
   (applies_at = `next_safe_boundary`). Contract-registered like the other kinds.
2. **`InterruptReason.STEERING_PAUSE`** (new) — a pause that carries NO approval decision;
   `ApprovalPayload.from_interrupt` yields an empty/continue-only payload.
3. **Dispatcher** (`_apply_control_item`): `PAUSE` sets `context.metadata
   ["steering_pause_requested"] = True` (does not mutate run_input). Returns APPLIED.
4. **`_execute_llm_call`** — right after `drain_step_boundary_controls` (steps.py:387),
   before the provider call: if `steering_pause_requested`, synthesize an
   `InterruptRequest(reason=STEERING_PAUSE, …)` + a minimal result carrier (`.interrupt`,
   `.traces=[]`), build the paused output via `_build_paused_output`, stash it with
   `set_terminal_output`, and return `RuntimeStepResult(next_step="done")`. The loop
   (`runner._drive_steps`) already surfaces a stashed terminal output → status `PAUSED`;
   the run RETAINS its lease on PAUSED (runner.py:464, existing policy).
5. **Resume** (`lifecycle/resume.py`): a `STEERING_PAUSE` pending interrupt resumes as a
   **continue** — no plan-approval branch, no decision required; clear the pause marker
   and re-drive from the checkpoint at `next_step="llm_call"`. `ResumeAction` reuses an
   existing "continue/approve" verb (see open decision #2).

This reuses `_build_paused_output`, the checkpoint, the lease policy, and the resume
entrypoint — the pause is durable + crash-safe for free.

## Alternatives considered

- **Cooperative in-loop block** (openhands holds the state lock during the LLM wait and
  blocks the loop). Rejected: agent-driver's loop is step-driven over durable checkpoints,
  not a long-lived blocking loop; a held-open coroutine would fight the checkpoint/resume
  and lease model and lose crash-safety. The synthesized-interrupt pause gives the same
  observable behavior (pause at the next boundary, resume later) durably.
- **Pause = CANCELLED + a "resumable" flag.** Rejected: overloads the terminal path;
  PAUSED already means exactly "stopped, resumable."

## Contract / SemVer

New `ControlKind.PAUSE`, `LiveMessageSemantic.PAUSE_CURRENT`, `InterruptReason.
STEERING_PAUSE`, one metadata key (`steering_pause_requested`). Additive; **Minor**.
Boundary-only (never mid-tool, never mid-LLM-await — Stop remains the only mid-flight
halt).

## Open decisions for sign-off

1. **Boundary-only pause** (checked after the drain, before the next LLM call) — accept, or
   also honor a pause requested during the tool phase (apply at the following boundary)?
   *Recommend boundary-only for phase-1; a tool-phase pause defers to the next boundary
   naturally.*
2. **Resume verb**: reuse `ResumeAction.APPROVE` as a plain continue for a STEERING_PAUSE,
   or add `ResumeAction.CONTINUE`? *Recommend a dedicated `CONTINUE` so a pause resume is
   not conflated with an approval grant in the audit.*
3. **Priority**: `PAUSE` is `NOW` (steer-current class) and, like Stop, should
   pre-empt/park pending non-Stop steers at the same boundary? *Recommend yes — a pause
   should win over queued steers, mirroring STOP_PREEMPTED.*
4. **Auto-pause on soft interrupt** (hermes Ctrl+C → goal auto-pause so an interrupt is
   recoverable). Out of scope for A3 phase-1 (needs the goal-loop layer); note as a
   follow-up.
