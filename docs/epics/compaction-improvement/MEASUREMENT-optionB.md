# Measurement — does compaction fire, how often, at what cost?

Live run (OpenRouter, `qwen/qwen3.6-plus`), 2026-08-06. De-risks Option B by answering
whether B1b/B2 matter in practice before building them. Reproduce with a run that keeps the
trimmed prompt above the compact threshold (large `trim_max_chars`, small
`context_window_estimate`) and compaction enabled.

## Findings

**1. Compaction only fires when the (post-trim) prompt exceeds the compact threshold.**
A ~5,481-token prompt (ratio 0.68) with an 8k window produced `delegate_or_summarize` but
`compaction_decision = none` — it did NOT fire. Only when the prompt crossed the threshold
(≥ compact_threshold, or ratio ≥ compact_ratio=0.75) did it fire. So with a SMALL trim budget
(the default `trim_max_chars`), trimming caps the prompt small, pressure stays low, and
**compaction rarely/never fires** — B1b/B2 are moot in that config. They matter only when a
host runs a large trim budget + large context.

**2. Under sustained pressure it re-summarises the full log EVERY turn — no amortization.**
Three turns, context held over the threshold:

| turn | state | used tokens | compaction fired | aux-summary INPUT tokens |
|---|---|---:|---|---:|
| 1 | compact_recommended | 7341 | yes (llm_full) | 12,575 |
| 2 | blocking | 7446 | yes (llm_full) | 12,566 |
| 3 | blocking | 7447 | yes (llm_full) | 12,569 |

**3 turns → 3 full aux-LLM summaries of ~12.5k largely-OVERLAPPING tokens each**, from scratch,
never reusing the prior summary. ~37.7k redundant summary-input tokens over 3 turns; a 20-turn
over-threshold run wastes ~250k tokens. This is exactly the cost **B2 (rolling/amortized
summary)** removes — confirmed empirically, not hypothetical.

**3. BUG-6 calibration is per-run, so it does not help across turns or on the first step.**
`chars_per_token` stayed 4.0 across the three turns because each turn was a separate `run_id`
(fresh `context.metadata`). The usage-calibrated ratio only applies within a multi-step run,
after at least one response. Short/first-step runs and turn-to-turn never benefit — a follow-up
could persist the calibrated ratio across a session/thread.

## Impact ranking (data-backed)

- **B2 (amortized summary)** — HIGH *when compaction fires*: ~12.5k redundant aux-input tokens
  per over-threshold step. Direct, measurable cost/latency win.
- **B1b (cheap tiers first + honest partial/BUG-7)** — HIGH *when compaction fires*: cheap
  deterministic tiers before the LLM summary would cut most of that aux cost; also fixes the
  BUG-7 lossy-partial correctness issue.
- **Gating question** — whether compaction fires at all depends on `trim_max_chars` vs the
  compact threshold. Worth confirming the real product (excel-ai) config: if it runs a small
  trim budget, trimming dominates and the compaction plane is largely dormant.
- **B3 (durable-log bounding)** — independent of the above; checkpoint growth on long runs.
- **BUG-6 phase-2 + cross-run calibration persistence** — lower, but the per-run reset (finding 3)
  is a cheap, real improvement.
