# Design Decision — Option B2: amortized rolling summary

Status: **proposed (awaiting sign-off).** Branch: `epic/compaction-optionB2`. Parent:
[`DESIGN-optionB-condenser-pipeline.md`](DESIGN-optionB-condenser-pipeline.md). Reference
implementation surveyed: hermes-agent per-turn micro-compaction
([`../../backlog/agent-driver-harness-analysis/epics/047-horizon-scan-2026-08-06.md`](../../backlog/agent-driver-harness-analysis/epics/047-horizon-scan-2026-08-06.md)).

## The gap B2 closes

`MEASUREMENT-optionB.md` (live, 2026-08-06) confirmed cost #1: under sustained
over-threshold pressure the `llm_full` path **re-summarises the full growing history from
scratch every step**, reusing nothing — 3 turns → 3 aux-LLM summaries of ~12.5k largely
overlapping tokens (~37.7k redundant input tokens over 3 turns, ~250k over 20). B2 makes
the summary **amortized**: fold the prior summary + only the newly-overflowed slice, one
small aux call per step instead of a full-history one.

## What B2 is on OUR architecture (the key simplification)

hermes mutates one in-memory message list AND keeps a session DB in step — the source of
its riskiest bug class (`archive_and_compact`, resume-double-load, append-only-flush
interactions). We do **not** inherit that: we already have the
immutable-durable-log (`context.metadata["protocol_messages"]`) / ephemeral-derived-View
(`request.messages`) split (see the Option-B pivotal finding). So for us the rolling
summary is a **cached derived artifact keyed by the log prefix it covers**, held in
`context.metadata`, never a destructive in-place DB splice. **We skip hermes's entire
DB-sync/resume-rehydrate slice.**

Precedent already in the tree: `session_memory` tracks `last_summarized_turn_index` and
selects `new_digests = [d for d in digests if d.turn_index > previous_turn]` — the exact
cursor arithmetic B2 needs, but for deterministic digest re-join, not an LLM fold.

## Mechanism

Per-run state in `context.metadata` (durable via checkpoint):
- `rolling_summary` — the cumulative summary text (structured, same shape `llm_full`
  produces today).
- `rolling_summary_covers_turn` — the highest turn index the summary already absorbs
  (the cursor). Starts at −1.

On a compaction firing (the existing eligibility/trigger is unchanged):
1. Select the **new slice** = protocol messages with `turn_index >
   rolling_summary_covers_turn`, up to the protected tail (recent turns + system head stay
   verbatim, exactly as today). User turns are never absorbed (invariant adopted from
   hermes).
2. One aux-LLM **merge** call: `rolling_summary + new_slice → updated rolling_summary`
   (a merge prompt, not a from-scratch summary of the whole history). Only the slice is
   new input; the prior summary is reused.
3. Update the cursor to the slice's max turn index; cache the updated summary.
4. Derive the View exactly as today: splice a single `compaction_summary` system marker
   (carrying the rolling summary) ahead of the first non-system retained message. The
   marker lives only in `request.messages` (ephemeral) — `protocol_messages` is untouched,
   so nothing is destroyed and resume rebuilds the View from the durable log.

Because the summary is prefix-keyed and the durable log is intact, **resume is free**: on
a resumed run the rolling summary is recomputed on first pressure from the log prefix (or
rehydrated from checkpointed metadata) — no hermes-style double-load loss is possible.

## Design knobs (documented `CompactionSettings`)

- `enable_rolling_summary` — **opt-in, default false.** Per-turn history rewriting breaks
  the provider prompt-cache prefix; default-off protects prompt-cache-sensitive hosts.
- `rolling_summary_every_n_turns` — cadence (default 1, clamped ≥1): the only lever that
  trades reclaim frequency for fewer cache breaks. An operator on a deep cache-discount
  provider raises it to make breaks episodic.
- `rolling_summary_defrag_threshold_tokens` — when the rolling summary itself grows past
  this, re-summarise *only the summary text* in one aux call (transcript-shape-neutral).
- Model choice is the headline cost knob (a pass runs under pressure every eligible turn):
  a small non-reasoning instruct model via the existing `compaction_model` / aux seam.

## Hardening — which hermes lessons apply to us

- **ADOPT:** never summarise user turns; distinct `MICRO`/rolling marker key so a full
  (`llm_full` batch) compaction and the rolling summary can coexist without one dropping
  the other's richer content; reset rolling state when a full compaction supersedes it;
  poison-slice guard (skip a slice that fails to summarise 3× and advance the cursor);
  duck-typed/disabled gate on the summariser callable.
- **SKIP (our View/log split removes the failure mode):** `archive_and_compact` DB-sync,
  resume-double-load rehydrate, and the alternation `repair_message_sequence` bug — our
  marker is in the ephemeral View and we have no in-place transcript-merge pass. Port the
  *lesson* of "cursor derived from post-mutation state" as prefix arithmetic, not the code.

## Integration ordering vs B1

B1a (condenser pipeline foundation) is built but **not wired** (B1b deferred). Two options:
- **(B2-direct)** Make the *existing* `_apply_llm_full_compaction` path rolling
  (fold-prior-summary) behind `enable_rolling_summary`, independent of the pipeline. Lowest
  risk, immediate payoff, no dependency on B1b.
- **(B2-as-condenser)** Land B1b first, then add a `RollingLlmCondenser` tier. Cleaner
  long-term but blocks B2 on the larger pipeline cutover.

Recommendation: **B2-direct** — the amortization win does not need the pipeline, and the
rolling path can later be lifted into a condenser when B1b lands.

## Cost / cache tradeoff

Per-turn history rewriting **breaks the prompt-cache prefix every eligible turn** — the
ephemeral-View split does NOT save this (the provider still sees a changed prefix). So B2
is a *trade*, not a pure saving, exactly as hermes documents. Mitigants: (a) opt-in
default-off; (b) the cadence knob; (c) the plane is dormant unless `trim_max_chars` is
large vs the compact threshold, so on small-trim hosts the break never happens and on
large-budget hosts the ~12.5k-tokens/step saving is exactly where the win is. The new
**occupancy telemetry** (`compaction_plane_dormant`, shipped) is the measurement to gate on
before enabling per host. Keep our existing circuit-breaker + idle-timeout liveness
(hermes lacks both).

## Contract / SemVer

New `CompactionSettings` fields (opt-in, default-off) + rolling-summary metadata keys.
Internal to `context/compaction/`; no public wire-contract change. **Minor**,
behaviour-preserving by default (rolling path only runs when explicitly enabled).

## Open decisions for sign-off

1. **Integration:** B2-direct (patch `_apply_llm_full_compaction` behind a flag) vs
   B2-as-condenser (gate on B1b)? *Recommend B2-direct.*
2. **Rolling state location:** per-run `context.metadata` keyed by covered turn index
   (recommended, checkpoint-durable), vs the `session_memory` artifact store seam
   (heavier, cross-thread)? *Recommend context.metadata.*
3. **Default cadence:** `every_n_turns=1` (max reclaim, max cache-breaks) vs a more
   cache-friendly default like 3? *Recommend 1 with opt-in off, matching hermes's final
   stance; hosts tune up for cache-discount providers.*
4. **Defrag threshold** starting value (hermes 2000 tok) — adopt as-is or derive from the
   window? *Recommend fixed 2000 to start; revisit with occupancy data.*
5. **Marker coexistence:** confirm the distinct rolling-marker key + full-compaction reset
   is enough given we also have the deterministic `session_memory` plane in play.
