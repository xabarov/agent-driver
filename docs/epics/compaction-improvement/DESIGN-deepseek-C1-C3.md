# DESIGN — compaction hardening C1–C3 (deepseek-harness–informed)

Status: proposed (2026-08-19). Extends this epic; does **not** create a new one.
Source of ideas: comparative survey of `reference/deepseek-harness` (`dsh`, Cordis,
"everything is a plugin") vs our runtime — see memory `deepseek-harness-survey`.

`dsh`'s clearest architectural edge over us is exactly this subsystem: **the session
log is the single source of truth, and the model's message history is a *cached fold*
over it** (`deriveMessages()`), while compaction is a **non-destructive, journaled**
operation that *shadows* a range rather than mutating it. We already stumbled into the
same immutable-log/derived-View shape by accident (`context.metadata["protocol_messages"]`
= durable log; `request.messages` = the ephemeral View that compaction rewrites and never
writes back). So C1 and C3 are **formalizations of what we half-have**, not green-field;
C2 is the already-drafted Option-B1 cutover.

## The three work packages

| WP | Idea (from `dsh`) | Fixes | Effort | Risk |
|----|-------------------|-------|--------|------|
| **C3** | cached derived-View + O(1) next-seq | RAM/CPU blowup **root** (`_next_seq` O(n)); imperative 3-writer drift | S | Low |
| **C1** | journaled non-destructive compaction (shadow-not-mutate) | BUG-7 (lossy partial reports success); no audit/replay of compaction | M | Med |
| **C2** | wire the `Condenser` pipeline as the compaction seam | hardcoded mode-decision tree; `condenser.py` built but dormant (B1b) | M | Med |

Sequencing: **C3 → C1 → C2.** C3 is a pure, isolated correctness win and gives the
`seq`/generation primitives C1 rides on; C1 turns compaction into durable log facts; C2
then swaps the decision tree for the pipeline behind a flag, emitting C1's facts.

---

## C3 — cached derived-View + O(1) next-seq (effort S)

### Root cause (verified)
`agent_driver/runtime/single_agent/lifecycle/journal.py:55-57`:

```python
def _next_seq(self, run_id: str) -> int:
    events = cast(list[RuntimeEvent], self._deps.event_log.list_for_run(run_id))
    return (max(event.seq for event in events) + 1) if events else 1
```

Every event emit re-materializes and scans the whole run log → **O(n) per emit, O(n²)
per run**. This is the RAM/CPU blowup that `CLAUDE.md` currently *backstops* (via
`default_max_steps=80` + budget-grace) rather than *fixes*. `dsh` keeps a running
high-water mark; so should we.

### Change
- Add a per-`run_id` high-water map on the journal mixin: `_seq_high: dict[str, int]`.
  `_next_seq` returns `_seq_high.get(run_id, 0) + 1` and updates it — O(1).
- **Correctness seam:** the map is a process-local cache, not the source of truth. On the
  first `_next_seq` for a `run_id` not in the map (cold resume / reattach), seed it once
  from `max(seq)` over the store (the current scan), then stay O(1). This keeps resume
  correct while removing the steady-state scan. A tiny helper `_seed_seq_high(run_id)`.
- **Derived-View cache (the `deriveMessages` analogue).** Where we assemble the model
  View from `protocol_messages`, add a generation-invalidated projection cache: fold only
  the new tail (`O(new nodes)`), rebuild fully only when a `replace_generation` counter
  bumps (C1 bumps it on a shadow). Landing point: the View assembly in
  `agent_driver/runtime/single_agent/llm_step/build.py` (the `protocol_messages` →
  request-messages path around `build.py:402-458`). Keep it behaviour-neutral in C3
  (same output, cheaper); C1 adds the shadow-skip.

### Tests
- `_next_seq` returns identical sequence to the old scan across emits (parametrized) and
  after a simulated cold-resume (map cleared) — same first value.
- Micro-benchmark guard: N=2000 emits stays sub-linear in wall-time vs the old path
  (marked `slow`, asserts a generous ceiling — not a flake-prone tight bound).
- Derived-View cache: fold of a growing `protocol_messages` equals a full re-fold at every
  prefix (property test), and rebuilds exactly once when the generation bumps.

### SemVer
Patch/minor — pure internal correctness, no public-surface or behaviour change.

---

## C1 — journaled non-destructive compaction (shadow-not-mutate) (effort M)

### Update (2026-08-19) — scope narrowed after tracing the real data flow

Before building the shadow machinery we traced how compaction actually mutates
messages. **Finding: our compaction is already non-destructive.** The durable log
`context.metadata["protocol_messages"]` is append-only and grown only by five
writers (tool-stage, steering redirect, research-gating, two control-dispatcher
paths); compaction reads a *fresh copy* of it into a throwaway per-step
`request.messages` and trims only that copy (`compaction_stage.py` assigns
`request.messages = …` at 229/259/817/1143/1208 and never writes back to
metadata). The reduction does not even persist across steps — the next step
re-reads the full log and re-trims. So the "shadow raw events instead of deleting
them" half of this WP is **already satisfied for free**; there is no destructive
mutation to fix.

That collapses C1 to the **honest-outcome** half, which is the concrete, named
bug:

- **BUG-7 (partial reports success on no progress) — DONE.** `_apply_partial_compaction`
  returned `success=True` unconditionally, even for an explicit `no_op` from
  `build_partial_compaction` or a rewrite that freed no chars, which *reset* the
  circuit breaker and masked the non-progress. It now measures `chars_freed`
  (`message_chars` before/after), reports `successful` only on real progress, and
  otherwise leaves the View untouched and records an honest `skipped`
  (`skip_reason: no_op | insufficient_progress`) via `complete_attempt(result=None)`
  — **neutral**: no false breaker reset, no unfair failure. `chars_freed` is now
  on both the successful and skipped `MEMORY_COMPACTED` payloads (durable audit).
  Tests: `tests/runtime/test_compaction_partial_honesty.py`.

Deferred (not built — would be speculative without a consumer): a
`find_orphaned_compactions(events)` helper (the START/terminal pairing that makes a
crash mid-compaction detectable already holds — `MEMORY_COMPACTION_STARTED` with no
`MEMORY_COMPACTED`); and enriching the `llm_full` outcome event with its
material-unit receipt (currently in `context.metadata` only). Pick these up when a
resume-preflight / audit consumer actually needs them.

### Idea (original plan, retained for context)
Today the compaction stage rewrites the ephemeral View and **never journals the operation
as a first-class fact**. A crash mid-compaction is invisible; a lossy partial that frees
nothing can still return "compacted" (BUG-7); the run's compaction history is not
auditable or replayable. `dsh` records three **log-only** events and shadows the replaced
range instead of deleting it.

### Change
- New `RuntimeEventType` members in `agent_driver/contracts/enums.py`:
  `COMPACTION_START`, `COMPACTION_SUMMARY`, `COMPACTION_END` (log-only — they do **not**
  enter the model View).
- New `agent_driver/context/compaction/journaled.py`:
  - `ShadowRange(start_seq, end_seq, shadowed_seqs: tuple[int,...], token_count)`.
  - `apply_journaled_compaction(ctx, result, emit)`:
    1. emit `COMPACTION_START` (opens the bracket / lock);
    2. emit `COMPACTION_SUMMARY` carrying `{summary_blocks, shadow: ShadowRange, provider,
       model, call_envelope}` — the summarize call is reconstructable from **log + code**;
    3. append the summary to `protocol_messages` as a **replace-marked** message that
       *shadows* the range; **do not delete** the shadowed messages;
    4. emit `COMPACTION_END` **last** → a crash leaves a detectable *orphaned*
       `COMPACTION_START` (no matching `END`), not a false "finished".
  - Bump the derived-View `replace_generation` (C3) so the View re-folds and skips the
    shadowed seqs.
- The View fold (C3) gains one rule: a message inside a shadowed range projects to
  nothing; the replace-marked summary projects in its place. Raw events stay in the log →
  transcripts / future session-query see the full history.
- **Honest exhaustion:** if the chosen tier freed nothing meaningful (single oversized
  unit), emit `COMPACTION_START` + a `COMPACTION_END{reason: exhausted}` with no summary
  shadow, and surface `exhausted=True` to the caller — never a fake success (this is the
  runtime-side half of the `condenser.py` `exhausted` guarantee; closes BUG-7).

### Tests
- Round-trip: after a journaled compaction, the raw shadowed events are still in the store;
  the derived View excludes them and includes the summary once.
- Orphaned-lock detection: a `COMPACTION_START` with no `END` is reported by a
  `find_orphaned_compactions(run_id)` helper (feeds resume preflight).
- BUG-7: a tier that frees nothing yields `exhausted=True` + no shadow, not "compacted".
- Replay: folding `[log prefix through COMPACTION_END]` reproduces the same View bytes.

### SemVer
Minor — additive event types + a new module; default runtime behaviour for hosts that
already compact becomes *more* correct (audit + honest exhaustion), no API break.

---

## C2 — wire the `Condenser` pipeline as the compaction seam (B1b) (effort M)

### Status: DONE (2026-08-20, flag default OFF — A/B gate pending)

Landed behind `CompactionSettings.use_condenser_pipeline` (default **False**;
`RunnerConfig.use_condenser_pipeline` proxy). When on, `_run_compaction_mode_dispatch`
routes transcript compaction to `_run_condenser_pipeline_dispatch`, which runs a
`CondenserPipeline` of the **model-free** tiers cheapest-first
(`agent_driver/context/compaction/condenser_tiers.py`: `ToolResultPruner` →
`ToolHistoryCondenser` → `PartialCondenser`) and:

- **fits under target from the model-free tiers → success with NO LLM call** (the
  novel win — verified by a test that fails if `_apply_llm_full_compaction` runs);
- **does not fit + `enable_llm_compaction` → delegates to the mature
  `_apply_llm_full_compaction`** path (request still original — no double-compaction),
  rather than re-implementing excerpt/provider/rolling-summary inside a condenser;
- **does not fit + no LLM tier → applies the model-free progress honestly
  (`fit=False`)**, or a neutral `skipped` when nothing was freed (same honest-outcome
  discipline as C1 — no false breaker reset).

`session_memory` compaction is unaffected (stays on the legacy plane). `span_collapse`
and `llm_full` were deliberately NOT wrapped as condensers — both need a provider, so
`span_collapse` stays unused and `llm_full` is reached by delegation. Tests:
`tests/context/test_condenser_tiers.py`, `tests/runtime/test_compaction_condenser_pipeline.py`;
flag-off path proven byte-identical by the existing suite (1439 green).

**A/B gate: RUN (2026-08-20) — default stays OFF.** Added an `eval compare
--treatment condenser_pipeline` axis (forced-pressure window so compaction fires on
the general suite; both arms compaction-on, only the flag differs) and ran it live on
the open-weight small tier. Result: **noise-dominated** — success_rate and latency
flip sign between 1 and 4 repeats, cost identical, judge quality Δ −0.10 at n=12 not
reproduced. No quality harm, but no clean neutral-or-better win to justify flipping.
Full write-up + the decisive-re-run recipe (tool-heavy SSB) in
[`MEASUREMENT-C2-condenser-ab.md`](MEASUREMENT-C2-condenser-ab.md). Decision: keep
`use_condenser_pipeline` **default OFF**; the seam ships opt-in. Do NOT benchmark-fit.

### Idea
`agent_driver/context/compaction/condenser.py` already ships the cost-ordered
`CondenserPipeline` with `minimum_progress` (anti-thrash) and honest `exhausted` — but its
own docstring says the live dispatch is **not** wired onto it; the current
`orchestrator.decide` mode tree (`llm_full` / `partial` / `session_memory` / `span_collapse`
/ `tool_clear` / `tool_history`) is an `if`-ladder, not a pipeline. B1b is the flagged
cutover.

### Change
- Port each existing mode to a `Condenser` (thin adapters over the current
  implementations — `ToolResultPruner` over `tool_clear`/`tool_history`,
  `SpanCollapseCondenser` over `span_collapse` (currently dead — wire it),
  `RollingSummaryCondenser` over the opt-in B2 rolling summary, `LlmFullCondenser` over
  `llm_full`).
- Assemble cheapest-first: model-free pruners → span-collapse → rolling summary →
  llm-full (last resort), so an LLM summary becomes a **no-op** when deterministic tiers
  already fit.
- Behind `CompactionSettings.use_condenser_pipeline` (default **off** — behaviour-neutral
  until proven). When on, the pipeline result feeds `apply_journaled_compaction` (C1), so
  C2 emits C1's durable facts.
- Keep `orchestrator.decide` as the default path until an A/B (`eval compare`, and
  excel-ai SSB per the epic's MEASUREMENT docs) shows the pipeline is at least neutral on
  quality-per-dollar. Do **not** benchmark-fit; the lever is structure + honesty.

### Tests
- Each ported condenser matches its legacy mode's output on a fixture (characterization).
- Pipeline: deterministic tier fitting the target skips the LLM tier entirely (no aux call).
- Flag off ⇒ byte-identical to today (snapshot).
- `exhausted` propagates from pipeline → journaled compaction → caller.

### SemVer
Minor — new opt-in config field; default path unchanged.

---

## Cross-cutting

- **Boundary (per repo `CLAUDE.md`):** all three stay domain-neutral runtime work. No
  domain memory policy (that's excel-ai). No benchmark-fitting.
- **Measurement:** reuse this epic's `MEASUREMENT-*.md` harness (excel-ai SSB A/B +
  `eval compare`) for C2's quality-per-dollar gate; C1/C3 are correctness and need only
  unit/property tests + the micro-benchmark guard.
- **CHANGELOG:** one `[Unreleased]` entry per WP as it lands.
- **Non-goals:** not a loop rewrite; the strict `model-visible ⟺ logged` invariant as a
  *wholesale* stance is explicitly **not** adopted — we take the cheap, high-leverage
  slice (cached fold + journaled shadow), not `dsh`'s full architecture.
