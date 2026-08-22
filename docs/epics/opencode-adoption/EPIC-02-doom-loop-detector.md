# EPIC-02 — Doom-loop / repetition detector (S)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `processor.ts` gates on 3 consecutive identical tool calls (same name +
`JSON.stringify(input)`) → a `doom_loop` permission ask.

## Finding: the detector already existed

The runtime **already** forces a final answer when the model repeats itself:
`_has_repeated_recent_tool_call(context)` (`tool_stage/guards.py`) detected the *last two*
tool calls being identical (same tool name + canonical args, result-independent), and
`_force_final_reason` returned `"repeated_tool_call"` when it fired — **default-on, no
policy profile required** (distinct from the policy-gated `_current_no_progress_repeat`,
which also keys on the result summary and is restricted to read-like tools). So our
behaviour was actually *stricter* than opencode (fires at 2, not 3).

The only real gap was that the threshold was **hardcoded at 2** and only ever compared
the last two calls.

## What was done

- Generalized `_has_repeated_recent_tool_call` from "last two identical" to "the last
  **N** calls all identical", where N is `RunnerConfig.repeat_call_guard_threshold`.
- New config field `RunnerConfig.repeat_call_guard_threshold` (default **2** — preserves
  the historical behaviour; **0/1 disables**; raise it, e.g. to 3 for opencode's leniency
  or higher for agents that legitimately repeat a call). Seeded into `context.metadata`
  by the tool stage (`setdefault`) so the `context`-only `_force_final_reason` path reads
  it; documented in `docs/runtime-metadata.md`.
- Removed the parallel guard drafted before the existing detector was found (no
  duplication).

Behaviour-neutral at the default (2). The force it triggers is the existing graceful
final-answer turn, not a hard stop.

Tests: `tests/runtime/test_doom_loop_repeat_guard.py` — default-2 fires on two identical;
tail-consecutive only (interruptions reset); threshold 3 needs three; 0/1 disables;
non-int falls back to 2. Broad runtime + contracts sweep green.

## Not done (deliberately)

- No dedicated `doom_loop` HITL *permission ask* (opencode routes it to an ask). Our
  action is the graceful force-final, which is the right domain-neutral default; a HITL
  variant could compose with the EPIC-04 approval seam later if a consumer wants it.
- Default left at 2, not bumped to opencode's 3 — that would be a behaviour change
  (later force); left to the host to raise.
