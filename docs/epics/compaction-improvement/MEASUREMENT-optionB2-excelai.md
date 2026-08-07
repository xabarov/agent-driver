# Measurement — Option B2 rolling summary + occupancy telemetry on excel-ai SSB

Date: 2026-08-07. Live: OpenRouter `deepseek/deepseek-v4-flash-0731`, excel-ai infra
(2xxxx). Harness: `../excel_ai/backend/scripts/ssb_rolling_ab.py` (temporary; extends the
`EXCEL_BENCH_ENABLE_COMPACTION` hook with `EXCEL_BENCH_ENABLE_ROLLING`). Smoke, N=1
Sheet-Level SSB task (seed=42); the signal is unambiguous even at N=1.

## Arms

- **OCC** — prod config (compaction OFF): validates occupancy telemetry.
- **BASE** — compaction forced ON (`EXCEL_BENCH_COMPACT_THRESHOLD=3000`), NO rolling:
  re-summarises the full growing history each firing.
- **ROLL** — same, + `enable_rolling_summary`: folds the prior summary + only the new
  slice (session_memory disabled in this arm so its marker-reset doesn't mask rolling).

Signal captured by monkeypatching `run_full_llm_compaction`: `history_excerpt` chars and
`prior_summary` chars per firing. Occupancy read from `context.metadata["token_pressure"]
["occupancy_pct"]` per step.

## Results

| arm  | pass | cell-acc | firings | aux excerpt chars / firing | aux total-input chars / firing | max occupancy |
|------|------|----------|---------|----------------------------|--------------------------------|---------------|
| OCC  | 1/1  | 1.000    | 0       | —                          | —                              | **0.252**     |
| BASE | 0/1  | 0.000    | 2       | [25101, **27745**]         | [25101, 27745]                 | 5.273¹        |
| ROLL | 1/1  | 1.000    | 2       | [25101, **2643**]          | [25101, **6805**]              | 5.823¹        |

¹ BASE/ROLL occupancy is against the artificial forced threshold (3000 tok), not
meaningful; only OCC's 0.252 (prod threshold) is.

## Findings

1. **Occupancy telemetry works; the plane is dormant on SSB.** Prod config → 0 firings,
   max occupancy 0.252 (25% of the trigger). `compaction_plane_dormant` is now a
   first-class metric — the gating fact we previously established by hand.
2. **Rolling amortization confirmed.** First firing is identical in both arms (25101) —
   rolling degrades to a normal full compaction on the first fire, as designed. Second
   firing: BASE re-summarises the full, still-growing history (27745, larger than the
   first); ROLL folds only the new slice — excerpt **27.7k → 2.6k (~10×)**, and the full
   aux input (slice + prior summary) **27.7k → 6.8k (~4×)**. On a run with M firings, BASE
   pays M full-history summaries; ROLL pays 1 full + (M−1) small slices, so the saving
   compounds.
3. **Quality preserved.** ROLL held cell-acc 1.0. (BASE's 0/0.00 on this single task is
   the hard-task/240s-timeout seen at N=5 earlier, not a rolling effect — N=1, don't
   over-read the pass delta.)

## Verdict

B2 does what it is designed to on the flagship: it kills the redundant re-summarization
when compaction fires, at preserved quality. The occupancy telemetry confirms the plane is
otherwise dormant on the single-turn SSB workload — so B2's value is realised on the
long-multi-turn sessions that actually keep the plane firing, and it should be enabled
there (opt-in), gated on the occupancy metric, with the cadence knob for cache-sensitive
hosts. Reproduce: run the script above with `AB_N=3` for a firmer read.
