# Measurement — compaction ON vs OFF on excel-ai's SSB workload

Date: 2026-08-06. Live: OpenRouter `deepseek/deepseek-v4-flash-0731`, excel-ai infra
(PG 25432 / Redis 26379 / MinIO 29000). Harness:
`../excel_ai/backend/scripts/ssb_compaction_ab.py` (temporary; mirrors the
`SSB_NEUTRAL_PERSONA` A/B pattern). Phoenix tracing on throughout.

## Why

The compaction epic's config-check found excel-ai runs with compaction **disabled**
(no `CompactionSettings` passed → `enable_compaction=False`), so the whole LLM-compaction
plane is dormant in the flagship. Question from the product side: *what are the optimal
prod settings — is enabling compaction better for excel-ai's real workload?* This A/B
answers it empirically instead of by assertion.

## Arms

An env-gated hook in `agent_creation.py` (inert unless the benchmark sets the flags):

- **OFF** — prod today: deterministic trimming + microcompaction only.
- **ON** — `enable_compaction + enable_llm_compaction + partial + session_memory`, at the
  **prod thresholds** (`token_compact_threshold = 0.85 × context_window`).
- **ON+force** — same, but `EXCEL_BENCH_COMPACT_THRESHOLD = 3000` tokens, i.e. the compact
  trigger is dropped low enough that compaction actually fires on ordinary tasks. This
  isolates *what compaction does when it runs*, since the prod thresholds never trip it.

Subset: first 5 Sheet-Level SSB tasks (seed=42). Small by design — a probe, not a full run.

## Results

| arm       | pass | avg cell-acc | avg tool-calls | avg wall-clock | compaction firings |
|-----------|------|--------------|----------------|----------------|--------------------|
| OFF       | 4/5  | 0.788        | 1.8            | 58 s           | 0                  |
| ON        | 4/5  | 0.788        | 1.0            | 56 s           | **0**              |
| ON+force  | 4/5  | 0.788        | 2.0            | **233 s**      | **17** (5/5 tasks) |

Per-task (ON+force firings): ssb_142-32 ×2, ssb_188-39 ×7, ssb_262-17 ×2, ssb_CF_6540 ×2,
ssb_536-37 ×4. The single persistent failure (`ssb_536-37`) fails under **all three** arms
— a hard task that exhausts the 240 s wall-clock cap; compaction neither causes nor fixes it.

## Findings

1. **Flipping the flag alone is inert on this workload.** OFF and ON are identical to the
   third decimal (pass, accuracy) with **zero** compaction firings — a single-turn SSB task
   never builds enough post-trim context to reach `0.85 × window` (≈170 K tokens on the
   200 K-window model). Enabling compaction changes nothing here.
2. **When forced to engage, compaction is quality-neutral but expensive.** ON+force holds
   the same 4/5 / 0.788 despite firing 17 aux-LLM summaries, at **~4× wall-clock** (233 s vs
   58 s) plus the extra summary calls. It doesn't lose correctness — but it doesn't recover
   the hard task either.
3. **Latent SDK bug surfaced + fixed.** The first forced firing hit
   `400 … "default" is not a valid model ID`: `compaction_model`'s `"default"` sentinel was
   shipped literally to the provider. Fixed in agent-driver (resolves to the run's model);
   see the CHANGELOG entry and `test_default_compaction_model_sentinel_resolves_to_run_model`.
   The excel-ai flagship would have hit this the moment it enabled compaction.

## Recommendation for excel-ai prod settings

**Keep compaction OFF for the SSB / single-turn edit workload.** It offers no quality gain
and, when it engages, imposes a large latency/token tax. The context path that *actually*
runs in prod is deterministic trimming + microcompaction — that is where tuning effort pays
off for this workload, not the LLM-compaction plane.

**Caveat — where compaction WOULD matter:** long *multi-turn* sessions that accumulate
context past the window (not exercised by single-turn SSB). There, pure trimming drops old
turns outright while an LLM summary keeps the gist. If/when that workload is prioritized,
compaction can now be enabled safely (sentinel fix landed) — but it should be **gated to
long-session contexts**, not switched on globally, and paired with the epic's B2
(amortized/rolling summary) so the every-step re-summarisation cost seen here is not paid in
full each turn.

## Reproduce

```bash
cd ../excel_ai/backend
set -a; . ../infra/.env; set +a
DATABASE_URL=postgresql://excel_ai:excel_ai_password@localhost:25432/excel_ai \
REDIS_URL=redis://localhost:26379/0 S3_ENDPOINT=http://localhost:29000 \
AWS_ACCESS_KEY_ID=minioadmin AWS_SECRET_ACCESS_KEY=minioadmin \
AGENT_TIMEOUT_SECONDS=240 AB_N=5 AB_INSTR=Sheet-Level \
.venv/bin/python scripts/ssb_compaction_ab.py
```
