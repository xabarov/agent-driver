# Design Decision — BUG-3: pressure-threshold ratio consistency

Status: **decided + implemented** (2026-08-06). Branch: `epic/compaction-bug3`.
Predecessor: Option A phase-1/1b + the typed-path ceiling follow-up (all merged).

**Sign-off (user):** `compact_ratio = 0.75`; do NOT unify the absolute-threshold formulas.
**Implemented:** `TokenPressureInput.compact_ratio = 0.75` (slots between delegate 0.45 and
blocking 0.92); `_pressure_state` compact branch is now
`used ≥ compact_threshold OR ratio ≥ compact_ratio`; the snapshot carries `compact_ratio`.
Default path unchanged (0.75·window); typed path now compacts at 0.75·window instead of
~0.90. Regressions: `test_compact_ratio_net_triggers_compaction_below_absolute_threshold`,
`test_pressure_ratio_ladder_is_ordered`.

## Precise finding (supersedes the earlier "reconcile the numbers")

`context/token_pressure.py::_pressure_state` classifies context pressure by combining
two mechanisms — absolute token thresholds AND window-relative ratio safety-nets:

| state | trigger |
|---|---|
| `blocking` | `used ≥ blocking_threshold` **OR** `ratio ≥ blocking_ratio (0.92)` |
| `compact_recommended` | `used ≥ compact_threshold` — **threshold only, NO ratio** |
| `delegate_or_summarize` | `ratio ≥ delegate_or_summarize_ratio (0.45)` |
| `early_warning` | `used ≥ warning_threshold` **OR** `ratio ≥ early_warning_ratio (0.35)` |

`ratio = used_tokens / context_window_estimate`. The absolute thresholds are computed
per path: **default** = `config_sections.for_context_window` → `0.35/0.75/0.92 × window`;
**typed** = `run_budget` → `0.75/0.90/0.98 × input_tokens`. The ratio nets are always the
`token_pressure` defaults (0.35/0.45/0.92), never per-path.

**Consequence:** the ratio nets already make `early_warning` (≈0.35·window) and `blocking`
(≈0.92·window) consistent across both paths — the differing absolute thresholds are masked
by the nets. The ONE state with no ratio net is **`compact_recommended`**, so it fires at
its raw absolute threshold: **0.75·window on the default path but ~0.90·input_tokens
(≈0.90·window) on the typed path.** Compaction — the state that actually matters for this
epic — is the only genuinely inconsistent trigger, and it has no window-relative floor.

## The decision

Give `compact_recommended` a window-relative safety-net ratio, exactly like the other
states, so compaction fires at a consistent point regardless of path.

- Add `compact_ratio: float` to `TokenPressureInput` (slots between `delegate_or_summarize_ratio`
  0.45 and `blocking_ratio` 0.92).
- In `_pressure_state`, the compact branch becomes
  `used ≥ compact_threshold OR ratio ≥ compact_ratio`.
- Thread `compact_ratio` through the pressure-input construction (it currently carries
  early/delegate/blocking ratios; add compact alongside).

This is behaviour-preserving on the default path when `compact_ratio` = the default-path
absolute ratio (0.75), and it makes the typed path compact at the same window point instead
of ~0.90. It also documents the ratio ladder as a complete, intentional net
(0.35 → 0.45 → **compact** → 0.92).

## Recommended value — open decision

`compact_ratio = 0.75`. Rationale: matches the existing default-path `compact_threshold`
ratio (`window·0.75`), so the default path is unchanged and the typed path is pulled into
line (compact at 0.75·window instead of ~0.90). Alternatives: 0.80 (slightly later compaction,
more context retained before summarising — modestly higher overflow risk) — pick from the
cost/quality trade-off. The blocking net (0.92) is unchanged, so there is always headroom
between compact and blocking.

## Non-goals / not this decision

- Reconciling the *absolute* threshold formulas (`config_sections` vs `run_budget`) into one
  base is NOT required — the ratio nets already govern the effective behaviour, and unifying
  the formulas is a larger refactor with no behavioural payoff once `compact_ratio` exists.
  Document that the ratio nets are authoritative and the absolute thresholds are a secondary
  trigger.
- BUG-6 (real tokenizer) is orthogonal (`ratio` still uses the chars/4 estimate; a real
  tokenizer improves `used_tokens_estimate` accuracy for all states at once, later).

## Contract / behaviour / SemVer

- Additive field `compact_ratio` on the internal `TokenPressureInput` (not a public wire
  contract). The pressure snapshot metadata gains a `compact_ratio` key (document it in
  `docs/runtime-metadata.md` if it is surfaced).
- **Minor.** Behaviour change: typed-budget runs now reach `compact_recommended` at
  ~0.75·window instead of ~0.90 — earlier, more consistent compaction. Default path unchanged
  at 0.75. Document in CHANGELOG + the epic.

## Test plan

- Unit (`token_pressure`): a used-token count between `compact_ratio·window` and
  `compact_threshold` yields `compact_recommended` via the ratio net (the new path); default-
  path parity (0.75) unchanged; the ratio ladder ordering (early < delegate < compact < blocking)
  is asserted so a future edit can't invert it.
- A typed-budget run compacts at ~0.75·window (was ~0.90) — an eligibility-level regression.
- Full suite; measure with `evals/context_compaction_runner.py`.

## Open decisions for sign-off

1. `compact_ratio` value — **recommend 0.75** (default-path parity). Alternative 0.80.
2. Confirm we are NOT unifying the absolute-threshold formulas this increment (recommend not).
