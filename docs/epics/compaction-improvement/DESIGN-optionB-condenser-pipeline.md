# Design Decision — Option B: condenser pipeline + amortized view

Status: proposed (research-first; awaiting sign-off on direction). Branch:
`epic/compaction-optionB`. Predecessor: Option A + BUG-3 + BUG-6 phase-1 (merged).

## Pivotal architecture finding (changes the Option-B framing)

Tracing the message flow settled the question that determines feasibility:

- The **durable conversation** is `context.metadata["protocol_messages"]` — a serialized
  message list, read at step start (`protocol_messages_from_metadata`) and written back
  after each turn (`tool_stage._update_tool_protocol_messages` loads it, appends the new
  assistant + tool turn, persists).
- Each step builds `request.messages` **from** `protocol_messages`, then compaction
  rewrites `request.messages` **in place**.
- Compaction writes `request.messages` but **never writes `protocol_messages`** (the only
  three writers are the redirect path, the research-gating path, and the turn-append —
  none is compaction). So the compacted result is **ephemeral**: it affects only the
  current provider call.

**Consequence:** we already have — unintentionally — the core of OpenHands' model: an
(almost) immutable durable log (`protocol_messages`) and a derived per-step View
(`request.messages`). The "destructive rewrite" worry was wrong. But two real costs fall
out of it:

1. **No amortization.** Under sustained pressure, compaction re-fires and re-summarises the
   FULL (still-growing) `protocol_messages` **every step** — an aux-LLM call per step, from
   scratch, never reusing the prior summary. This is the opposite of OpenHands'
   `RollingCondenser` (fold the prior summary, summarise only the newly-overflowed slice).
2. **Unbounded durable log.** `protocol_messages` never shrinks, so the checkpoint grows for
   the whole run.

_(Open item: quantify the per-step re-summarisation cost from a real trace before committing
to B2 — this is the research step that sizes the payoff.)_

## What Option B should actually be for us

Given the split already exists, Option B is NOT "introduce an immutable log" — it is:

- **B1 — Condenser pipeline.** Refactor the monolithic View derivation (build's trim +
  the `session_memory → llm_full → partial` mode dispatch) into an explicit **cost-ordered
  pipeline of composable condensers**, each able to short-circuit the next:
  tool-result-clear → content-mask → partial-keep-tail → summary → session-memory. Wire in
  the currently-**dead** `span_collapse` / `tool_clear` primitives; make `partial`
  token-aware and honest (BUG-7 — stop reporting `success` on a lossy fallback / resetting
  the breaker); add OpenHands' `minimum_progress` anti-thrash as a first-class knob. This is
  a bounded refactor of the existing derivation on top of the already-non-destructive model.
- **B2 — Amortized (rolling) summary + view cache.** Cache the derived summary/view (keyed by
  the log prefix it covers) and update it incrementally instead of re-summarising the full
  log each step. Directly kills cost #1. This is the biggest efficiency win and the closest
  port of OpenHands' `RollingCondenser` + `summary_offset` splice.
- **B3 — (optional, deepest) durable-log bounding.** Evict/summarise the DURABLE
  `protocol_messages` (or spill it to an artifact) to bound checkpoint growth (cost #2).
  Touches durability/resume + checkpoint format — highest risk; likely a separate epic.

## Recommendation

**Sequence B1 → B2; treat B3 as a separate later epic.** Start B1 (pipeline + dead-primitive
wiring + BUG-7 + anti-thrash) because it is a bounded refactor with immediate quality/cost
wins and no durability-format change. B2 (amortization) is the high-value efficiency follow-up
— but gate it on a **measurement** of the real per-step re-summarisation cost first (a trace /
`evals/context_compaction_runner.py` run), so we size B2's payoff before building it. B3 only
if the checkpoint-growth cost proves material.

Each of B1/B2 lands as its own test-gated increment (this epic's pattern), keeping default
behaviour green behind flags where behaviour shifts, measured with the eval runner.

## Contract / behaviour / SemVer (B1)

- A `Condenser` protocol + a `CondenserPipeline` internal to `context/compaction/` (not a
  public wire contract). The existing modes become condensers registered in the pipeline;
  the public `agent_driver.execution`-style surface is untouched. `minimum_progress` and the
  pipeline order become documented `RunnerConfig`/compaction knobs.
- **Minor**, behaviour-preserving by default (same modes, same order) until the cheap tiers
  are enabled; the honest-partial change (BUG-7) is a correctness fix.

## Open decisions for sign-off

1. **Scope now:** B1 only (pipeline refactor), or B1 + measure-then-B2? **Recommend B1 first**,
   then a measured decision on B2.
2. **B1 as a design-decision-then-implement (like BUG-3/6) or a Plan-agent-led larger effort?**
   It is bigger than the bug fixes — **recommend a dedicated B1 design decision** (condenser
   protocol shape, pipeline order, how the existing modes map on) before implementing.
3. **B3 (durable-log bounding)** — confirm it is out of scope for this epic (separate epic).
