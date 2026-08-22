# EPIC-05 — Structured summary template + rolling-update contract (S)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `core/session/compaction.ts` `SUMMARY_TEMPLATE` (Objective / Important Details
/ Work State {Completed, Active, Blocked} / Next Move / Relevant Files) and
`SUMMARY_UPDATE_INSTRUCTIONS` (the prior summary is discarded — carry forward or lose it;
newer conversation wins on conflict; preserve exact paths/commands/errors).

## What we already had

`build_full_compaction_prompt` (`context/compaction/prompts.py`) already emits a
**structured JSON** persisted summary (not markdown) with fixed keys — `request_intent`
(≈Objective), `key_concepts` (≈Important Details), `files_code` (≈Relevant Files),
`errors_fixes`, `problems` (≈Blocked), `user_messages`, `pending_tasks` + `next_step`
(≈Next Move), `current_work` (≈Active) — plus B2 rolling (fold a new slice into a
`prior_summary`) and hermes language-preservation. So most of opencode's skeleton was
present; the JSON shape is deliberately kept (the parser/`_splice_summary_message`/rolling
cursor all depend on it — see [[compaction-improvement-epic]]).

## The two real gaps, now closed

1. **The "Completed" work-state bucket.** We had Active (`current_work`), Blocked
   (`problems`) and Next-Move (`pending_tasks`/`next_step`) but **no explicit Completed
   bucket** — finished work blurred into `current_work` and never got cleanly retired.
   Added a required `completed_work` key + an explicit bucketing instruction
   (completed = finished/verified/changes-made; current = in-progress; problems =
   blockers; pending/next = remaining moves). Added to `REQUIRED_SUMMARY_KEYS`
   (`context/compaction/llm_full.py`) so validation enforces it; the summary dict is
   serialized generically, so no per-key render code changed.

2. **The carry-forward-or-lose rolling contract.** The old rolling prompt said only
   "keep all still-relevant content." Ported opencode's stronger update contract into the
   `prior_summary` branch: the prior summary is **discarded** after this step — anything
   not carried is lost; **carry forward** objectives/constraints/user-directives/decisions/
   parallel-workstreams even when the new slice omits them, dropping only what is finished;
   the newer slice **wins on conflict** (state the corrected fact, drop the stale claim);
   **move** now-finished items from `current_work` → `completed_work`; update a cleared
   blocker while keeping detail still needed; refresh `request_intent` + `next_step` to the
   current state.

Also added opencode's **verbatim-preservation rule** to the shared header: preserve exact
file paths, symbols, function names, commands, error strings, URLs, and identifiers —
never paraphrase or elide. Resume correctness depends on those surviving compaction.

Pure prompt/contract text + one new JSON key; no control-flow change. Files:
`context/compaction/prompts.py`, `context/compaction/llm_full.py`. Tests:
`tests/context/test_llm_compaction_prompt.py` (new: work-state buckets + preservation rule
present; rolling carry-forward contract + conflict rule present, absent in the non-rolling
prompt) plus the three existing fake-summary payloads updated with `completed_work`. Full
`tests/context` + all compaction/rolling/summary tests green.

## Not done (deliberately)

- **Kept the JSON summary shape**, not opencode's markdown `<template>`. Our downstream
  (JSON parse, `_splice_summary_message`, the `rolling_summary` metadata cursor) is
  JSON-shaped; a markdown swap would be churn with no resume-quality gain.
- **`session_memory` deterministic extraction left as-is.** That path
  (`session_memory_extract.py`) is regex/heuristic, not an LLM prompt, so there is no
  template to strengthen; a `completed_work` field there would be a contract change with
  its own blast radius — out of scope for this prompt-only S.
