# EPIC-10 — Provider-overflow (413) emergency compaction (M)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `overflow.ts` / `processCompaction` — when the provider rejects the prompt as
too long, run a last-resort compaction (strip large/media tool payloads, force a head
summary, replay the last user turn) instead of failing.

## What we already had

A real reactive-overflow path already exists (`context_window_recovery.py` +
`_overflow_recovery` in `llm_step`): `is_context_window_error` detects the provider's
`context_length_exceeded` / `input_too_long` / 413-class error, and on the first hit
`_recover` force-compacts (`apply_compaction_if_eligible(token_pressure_state="blocking")`)
and rebuilds a smaller request, bounded by the reactive-compaction circuit breaker.

## The gap

The single overflow retry may not free enough. When `enable_compaction=False` (the
flagship consumer's path) the forced compaction runs only the graduated pre-passes — no
LLM/partial summary — and if the bulk is a **large or media payload** (a giant tool result
within `keep_recent`, or an embedded base64 blob in a user turn), the rebuilt request is
still over the hard window → the retry fails and the run escalates to `RUN_FAILED`. That is
exactly the case opencode's aggressive `overflow.ts` handles.

## What was built

`emergency_strip_oversized_payloads(messages, *, keep_recent_tool_results=1,
max_message_chars=20_000)` (pure, in `context_window_recovery.py`) — a last-resort strip,
more aggressive than the graduated pre-passes because the prompt is *already* over the hard
window:

- wholesale-clears the CONTENT of OLD tool results (keeping only the newest
  `keep_recent_tool_results`, default 1 vs the live pruner's 3);
- hard-caps ANY remaining message — tool result or a giant user/assistant turn carrying an
  embedded blob / base64 **media** — whose content exceeds `max_message_chars` to its head
  plus a dropped-count marker;
- preserves message order and `tool_call_id` pairing (only `content` shrinks); idempotent
  (a re-run is a no-op via the cleared/truncation sentinels).

Wired into `_overflow_recovery._recover` **on the rebuilt request** (the recovery rebuilds
from context, so stripping must happen after the rebuild), gated by
`RunnerConfig.overflow_emergency_strip_enabled` (**default True** — it only fires on an
actual overflow, already a failure state) with `overflow_strip_max_message_chars` (20k).
Emits the typed `context_overflow_emergency_strip` audit (`{cleared, truncated,
chars_saved, …}`) into run metadata. This guarantees the single overflow retry is
materially smaller even when LLM compaction is disabled — closing the case our
*estimate*-based proactive trigger and the LLM-summary path both miss.

Tests: `tests/runtime/test_overflow_emergency_strip.py` (clears old keeping recent; hard-caps
an oversized blob; leaves small messages; idempotent; keep-zero clears all). The existing
`test_context_window_recovery.py` / `test_context_overflow_recovery.py` + full
`tests/runtime` + `tests/context` sweeps stay green.

## Mapping to opencode

- **strip large/media tool payloads** → the wholesale clear + oversized-content hard-cap
  (media lives inline in `content` as base64 at this layer, so a size cap strips it).
- **force a head summary** → the existing `apply_compaction_if_eligible("blocking")` +
  rebuild already run first; the strip is the guaranteed floor beneath it.
- **replay the last user turn / continue** → the rebuild replays the current turn; the
  strip caps (rather than drops) an oversized last user turn so its intent survives.
- **typed diagnostic** → the `context_overflow_emergency_strip` metadata audit +
  `record_reactive_compaction` accounting.
