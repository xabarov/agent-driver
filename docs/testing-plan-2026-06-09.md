# Testing & Comparison Plan (low-budget, open-weight via OpenRouter)

Status: planning. Date: 2026-06-09.

Purpose: keep our offline-first testing discipline while adding a small, cheap
**live** comparison tier that measures the runtime-mechanic improvements from
[Improvement Plan E1–E8](improvement-plan-e1-e8-2026-06-09.md). Design goals:
keep the value, minimize the budget.

Decisions fixed for this plan:

- **Live provider: OpenRouter only.** No local Ollama tier. All live runs go
  through OpenRouter using **open-weight** models (e.g. Qwen / Llama / DeepSeek
  class). Frontier models are not required — we compare *harness mechanics at a
  fixed model*, which is methodologically valid (2026 consensus: results are
  shaped by the harness as much as the model).
- **Tasks are general, not coding.** Coding agents are well-served elsewhere;
  our suite is tool-use (τ-bench-style), multi-turn dialog, retrieval-lite,
  summarization, planning/todo, and cross-turn memory recall. These also
  exercise our mechanics (compaction, memory, hooks, permissions, subagents,
  rubric) more directly than coding tasks — and cost 3–4× fewer tokens.
- **Hard cost ceiling per suite run** via the existing `--cost-budget-usd` +
  cost-ledger, so a runaway loop cannot overspend.

## Current testing state (what we already have)

- **Offline-first, deterministic:** `FakeProvider`, pytest (~1960 tests / 272
  files), black/isort, pylint 10/10 on new modules.
- **CI** (`.gitlab-ci.yml`, offline only): full `pytest` excluding `tests/llm`
  and `live_smoke` with `-m "not live"`; the selftest harness
  `tools/selftest/run.py` (rubric scoring on the fake provider); and
  `agent-driver eval run --provider fake --offline --suite regression`.
- **Eval harness already built** (~70% of what a comparison needs):
  `agent_driver/evals/` — `replay`, `persisted_replay`, `context_quality_gate`,
  `evaluators`, `runner`; plus `batch/` (BatchRunner + TrajectoryStore +
  `compress`) and the **cost ledger** with `--cost-budget-usd`.
- **Gated live tests exist** (not in CI; require keys): `tests/llm/
  test_live_providers.py`, `tests/runtime/test_live_subagent_openrouter.py`,
  `tests/runtime/test_live_context_quality_openrouter.py` — i.e. the OpenRouter
  plumbing is already in place.

What's missing for a rigorous low-cost comparison: **N-run repetition with
median/percentile aggregation**, **cost/latency-per-task reporting**, an
**open-weight model preset**, and a **general task suite**. That is T0 below.

## Methodology best practices (and what they mean for us)

From the 2026 literature (sources at the bottom):

- **Fix metrics before running** — task-success, latency p50/p95,
  **cost-per-task**, **N-run reliability**, tool-use success rate. Adding metrics
  after seeing results = cherry-picking.
- **Handle stochasticity** — run each config **≥5×** at temperature > 0; report
  **median + 5–95% interval**, never the best score (one pass can hide ~20-pt
  variance).
- **Change one dimension at a time** — fix a baseline (harness + model + host),
  vary a single axis, so a delta is attributable.
- **Harness matters as much as model** — so comparing our mechanics (E1–E8) at a
  fixed open-weight model is sound and cheap.

Implication: most E-feature validation does **not** depend on task pass-rate —
it's about cost-per-task, cache-hit-rate, latency, ordering, and "was the bad
file blocked". Open-weight models are fine for these; weak tool-following adds
little noise.

## Test tiers

### Tier A — Offline, deterministic ·  cost $0  (always, in CI)

Correctness of every E-feature via `FakeProvider` + replay + selftest rubric.
This stays the primary gate; every E item ships with Tier-A tests.

### Tier B — Live, OpenRouter open-weight ·  the only spend  (on demand)

Statistical comparison and realistic cost/latency. Run a fixed open-weight
model at temp > 0, N ≥ 5, baseline vs treatment, one axis at a time. Recorded,
reproducible numbers (cost-per-task, cache-hit-rate, p50/p95, success).

Model preset (open-weight, OpenRouter): pick one **mid** model for
success-sensitive metrics and optionally one **small** for cheap behavioral/
latency checks. Pin exact model ids + temperature in the suite config.

## Budget (OpenRouter open-weight, general tasks)

Assumption: a general task ≈ ~30k tokens total; open-weight blended ≈ $0.25/M →
**~$0.01 / task-run**.

| Tier | Scope | Task-runs | Cost |
| --- | --- | --- | --- |
| A — offline | correctness of all E-features | n/a | **$0** |
| B — E-feature validation | 5 features × (baseline+treatment) × 5 runs × 30 tasks ≈ 1500 | 1500 | **~$15–30** |
| B — cross-harness suite | 30 tasks × 5 runs × 3 model tiers ≈ 450 | 450 | **~$10–15** |
| **Full round** | | | **~$30–60** |
| **Minimal first round** | one feature, one model, N=5, 30 tasks | ~300 | **~$10–20** |

This is a **~10–30× reduction** vs a frontier+coding round (~$500–1500), from
three stacked levers: open-weight pricing (~10–40×), shorter general tasks
(~3–4×), and fixed-model harness comparison (no frontier needed). An optional
tiny frontier reserve (~$10–20, occasional) can confirm conclusions transfer —
not required.

**Target ceiling: $60/round (with rerun headroom).** Default `--cost-budget-usd`
on a suite run set conservatively (e.g. $5) so a single sweep can't overspend.

## T0 — Eval infrastructure (prerequisite, offline-testable, $0 to build)

- [ ] **N-run repetition** over the BatchRunner: run each item K times at temp>0,
      keyed by `(item_id, run_index)`.
- [ ] **Aggregation**: median + 5–95% interval per metric (success, tokens, USD,
      latency); never report best-of.
- [ ] **Cost/latency-per-task reporting**: extend `BatchReport` (or a new
      `ComparisonReport`) with per-task USD (from the cost ledger), p50/p95
      latency, and cache-hit-rate.
- [ ] **Open-weight model preset**: a small config naming the OpenRouter
      open-weight model id(s) + temperature; wired into the eval CLI.
- [ ] **General task suite** (non-coding): tool-use / multi-turn dialog /
      retrieval-lite / summarization / planning / memory-recall scenarios under
      `agent_driver/evals/` (or `tools/selftest/scenarios`).
- [ ] **Cost ceiling**: default `--cost-budget-usd` on a suite run; abort + report
      partial results when hit.
- [ ] **Baseline-vs-treatment harness**: run the same suite with a capability
      flag off vs on, emit a side-by-side delta table.
- [ ] Tier-A tests for all of the above on `FakeProvider` (deterministic counts,
      aggregation math, budget-abort path).

**Done when:** `agent-driver eval run` (or a new `eval compare`) executes the
general suite N times on the open-weight preset, enforces the cost ceiling, and
emits median+interval cost/latency/success — all unit-tested offline.

## Per-feature evaluation mapping

How each E-item is measured (Tier A always; Tier B where a model is needed):

| Item | Tier-A (offline, $0) | Tier-B (live, OpenRouter) |
| --- | --- | --- |
| E1 auxiliary routing | aux provider used for compaction/grading/memory; separate cost tag | cost-per-task delta (main vs main+aux) at equal success |
| E5 arg truncation | old args clipped + audited pre-compaction | token delta before compaction |
| E2 project memory | layering/override/cap | grounding/success delta with vs without project files |
| E3 injection scanner | malicious fixture blocked; clean passes | n/a (deterministic) |
| E4 parallel tools | ordering + concurrency bound; faster on fake-delay | p50/p95 latency delta on multi-tool tasks |
| E6 subagent routing | map hit / override / default | cost delta from cheap roles |
| E7 composite FS | per-backend + routing | n/a (deterministic) |
| E8 sanitization | surrogate/non-ASCII/JSON fixtures | n/a (deterministic) |

Most rows are decided offline; live runs only quantify cost/latency/success
deltas where a real model is genuinely required.

## References

Methodology (web):

- [AI Agent Framework Scorecard 2026 — Rapid Claw](https://rapidclaw.dev/blog/ai-agent-benchmarks-2026)
- [Building Effective AI Coding Agents for the Terminal (arXiv 2603.05344)](https://arxiv.org/pdf/2603.05344)
- [Top 7 Benchmarks That Matter for Agentic Reasoning — MarkTechPost](https://www.marktechpost.com/2026/04/26/top-7-benchmarks-that-actually-matter-for-agentic-reasoning-in-large-language-models/)
- [Lost in Simulation: LLM-Simulated Users are Unreliable Proxies (arXiv 2601.17087)](https://arxiv.org/pdf/2601.17087)
- [UTBoost: Rigorous Evaluation of Coding Agents on SWE-Bench (arXiv 2506.09289)](https://arxiv.org/pdf/2506.09289)

Internal: [Improvement Plan E1–E8](improvement-plan-e1-e8-2026-06-09.md),
[review-cycle-2](review-cycle-2-2026-06-09.md), existing eval harness under
`agent_driver/evals/` and `tools/selftest/`.
