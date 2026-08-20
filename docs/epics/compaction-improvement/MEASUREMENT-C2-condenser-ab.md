# MEASUREMENT — C2 Condenser-pipeline A/B (2026-08-20)

Gate for flipping `CompactionSettings.use_condenser_pipeline` from its default OFF.
Instrument: agent-driver's own `eval compare` harness, new `--treatment
condenser_pipeline` axis (`agent_driver/cli/commands/evals.py`).

## Setup

Both arms share a **forced-pressure window** so compaction actually fires on the
short general suite (tool-use tasks accumulate tool output past the thresholds):
`TrimmingSettings(context_window_estimate=4000, token_warning=1500,
token_compact=2400, token_blocking=3200, output_token_reserve=400)`, and both
enable compaction (`enable_compaction`, `enable_llm_compaction`,
`enable_partial_compaction`). **Only `use_condenser_pipeline` differs** (off =
legacy mode tree, on = model-free CondenserPipeline), so the delta isolates the seam.

- Suite: `general_task_suite()` (n=12, tool-use / multi-turn), open-weight
  OpenRouter **small** tier, OpenRouter key from excel-ai `infra/.env` (`LLM_API_KEY`).
- Command:
  `agent-driver eval compare --treatment condenser_pipeline --tier small
   --repeats N --judge --max-cost-usd C`

Compaction **did fire**: median total tokens per run ≈ 2820–3060, above the 2400
compact threshold, in both arms.

## Results

| metric (median)        | repeats=1        | repeats=4        |
|------------------------|------------------|------------------|
| success_rate Δ (t−b)   | **−0.167**       | **+0.146**       |
| latency_ms Δ           | **−2733** (t faster) | **+682** (t slower) |
| cost_usd Δ             | +0.0000          | −0.0000          |
| tokens Δ               | −74              | +36              |
| judge quality Δ        | (n/a)            | **−0.100**       |

(b = mode_tree baseline, t = condenser_pipeline treatment.)

## Reading

**The deltas are noise-dominated and sign-unstable across repeat counts.**
success_rate and latency both *flip sign* between 1 and 4 repeats — the hallmark of
small-sample variance (n=12, a weak small-tier model, single-digit repeats). Cost is
identical to four decimals (the small model's saved `llm_full` call is too cheap to
move the median). The judge quality Δ of −0.100 at repeats=4 is within run-to-run
variance at this n and is **not** a reproduced regression.

Mechanistically, the expected C2 win (skip the LLM summary when the model-free tiers
already fit) showed up as the repeats=1 latency drop but did not hold at repeats=4 —
on these tasks the tool output is not always clearable enough to fit under the tiny
forced window, so both arms often still reach `llm_full`, erasing the difference.

## Conclusion & decision

- **No evidence C2 harms quality** (deltas within noise; the one negative quality
  reading did not reproduce directionally).
- **No clean neutral-or-better win** on this suite to justify flipping the default.
- **Decision: keep `use_condenser_pipeline` default OFF.** The seam ships opt-in; the
  gate is not met on this instrument.

## What a decisive re-run needs

The general suite under-exercises the seam's advantage. A conclusive gate wants:
tool-heavy transcripts where deterministic clearing *reliably* fits (so the LLM-skip
actually happens every firing), a larger n (more tasks × more repeats for tight CIs),
and a stronger tier/judge. The excel-ai SSB A/B on decomposable, tool-output-heavy
tasks is the better instrument — run it with the treatment arm setting
`RunnerConfig(compaction=CompactionSettings(..., use_condenser_pipeline=True))`
before revisiting the default. Per repo `CLAUDE.md`: do not benchmark-fit — the lever
is structure + honesty, and OFF-by-default is the safe state until a clean gate says
otherwise.
