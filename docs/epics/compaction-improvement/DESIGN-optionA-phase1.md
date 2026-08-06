# Design Decision — Option A, Phase 1

Status: **decided + partially implemented** (2026-08-06). Branch:
`epic/compaction-improvement`. Predecessor: research phase ([`RESEARCH.md`](RESEARCH.md),
[`BUGS.md`](BUGS.md)).

**Sign-off (user):** (1) `UNRESOLVED_MODEL_CONTEXT_WINDOW = 128_000`; (2) ceiling →
window fraction (drop the fixed 262144); (3) default-on + loud diagnostic (no opt-in flag).

**Implemented (this branch):**
- Change 1 (BUG-2) — DONE. `UNRESOLVED_MODEL_CONTEXT_WINDOW = 128_000`; `resolved_for_model`
  falls back to it with `context_window_source="unresolved_fallback"`; a once-per-run
  `context_window_unresolved_fallback` WARNING fires at the request-build site (registered
  in `docs/status-protocol.md`, metadata key documented).
- Change 3 (BUG-4, data-loss) — DONE. One `_is_protected_message` predicate now used by both
  the excerpt and `_retained_messages_after_full_compaction`; evidence/material-hash messages
  survive. Regression: `tests/runtime/test_compaction_budget_correctness.py`.
- Change 2 (BUG-1) — DONE (the isolated part). `_scaled_context_char_cap`'s fixed 262144
  ceiling → `COMPACTION_INPUT_WINDOW_FRACTION` (0.8) of the window char budget; the absolute
  constant is now a 4M memory backstop only.

**Deferred to phase-1b (larger, needs measurement):**
- BUG-5 — the `runner_defaults` path (no typed `RunContextBudget`) still derives `max_chars`
  from `trim_max_chars` (6000) and `max_compaction_chars` from `ptl_retry_max_chars` (4000),
  disconnected from the resolved window (and `input_tokens = max_chars//4 = 1500`). Making
  these window-derived is a wide behaviour shift on every default-path run and touches the
  budget contract (`contracts/context/run_budget.py::MAX_RUN_COMPACTION_CHARS`); it deserves
  its own increment validated with `evals/context_compaction_runner.py`, not a rushed end-of-
  turn change. BUG-3's scaling-formula reconciliation rides along with it.

## Phase-1b diagnosis (before implementing)

The exact mechanism (confirmed by tracing): `enable_ptl_retry` defaults **True**, so the
llm-full excerpt IS clipped by `_scaled_context_char_cap` on every default-path run — to
~4000 chars, because both inputs to the cap are static: `max_compaction_chars =
ptl_retry_max_chars (4000)` and the cap's window bound = `budget.max_chars = trim_max_chars
(6000)`. So "full" compaction summarises a sliver of history regardless of a 128K window.

**The trap:** `budget.max_chars` is **dual-purpose** — `context/trimming/deterministic.py:531`
uses it as the deterministic-trimming char budget too. Repointing `max_chars` to the window
would silently DISABLE trimming (trim-to-510K = keep everything). So the fix must decouple:

1. Derive `max_compaction_chars` (which only the compaction cap + diagnostics read) from the
   resolved window in `_run_context_budget_defaults`, leaving `max_chars` = `trim_max_chars`
   for the trimming plane.
2. Make `_scaled_context_char_cap` compute its window char budget from
   `effective_context_budget["context_window_estimate"]` (× chars/token, minus output
   reserve), NOT from `max_chars` (the trim budget). Then the compaction cap scales with the
   real window (× `COMPACTION_INPUT_WINDOW_FRACTION`) instead of the 6000 trim budget.

Effect: on a 128K default-path run the excerpt cap rises from ~4000 to ~0.8·(window−reserve)·4
chars — more complete summaries at higher aux-compaction cost (measured via the eval runner).
The typed-budget path's contract ceiling (`MAX_RUN_COMPACTION_CHARS`) is a separate follow-up.

**Phase-1b — IMPLEMENTED (2026-08-06).** Both changes landed:
(1) `_run_context_budget_defaults.max_compaction_chars` = `max(ptl_retry_max_chars,
(window−reserve)·4)`; (2) `_scaled_context_char_cap` computes its window char budget from
`context_window_estimate` (not the trim `max_chars`). Blast radius: exactly 1 golden
(`test_typed_output_budget…` typed-path `max_compaction_chars` 5625→262144, now bounded by
the contract ceiling — an improvement). Eval runner (`render_context_compaction_report`)
shows strategies still function (session_memory recall 1.0, full_llm 0.75). Regression:
`test_scaled_cap_uses_window_not_trim_budget`. Full suite green.

**Remaining follow-ups (phase-1c / Option-B adjacent):** the typed-path
`MAX_RUN_COMPACTION_CHARS = 262144` contract ceiling (fractionalise it); BUG-3 (reconcile
the `config_sections` 0.35/0.75/0.92 vs `run_budget` 0.75/0.90/0.98 ratio sets + document);
BUG-6 (real/provider tokenizer replacing the `_COMPACTION_CHARS_PER_TOKEN=4` and every other
hardcoded 4).

Option A is the **budget-correctness foundation**: most compaction defects are
downstream of "the runtime doesn't know the real context window, so every char/token
cap is wrong." Phase 1 lands the three highest-severity, lowest-architecture fixes.
No new compaction architecture (that is Option B). Live provider probing stays out of
scope (epic-017 deferred it; the runtime has no network by default).

## Scope — three changes

1. **Robust context-window default** (BUG-2) — stop silently assuming a 12K window on
   unresolved models; single-source the constant.
2. **Window-fractional compaction caps** (BUG-1, BUG-3, BUG-5) — derive the summariser
   char budget from the resolved window on every path; kill the absolute 262144 ceiling.
3. **Unify protected-message retention** (BUG-4) — the post-summary keep-set must honour
   the same protection predicate as the excerpt, so evidence-flagged messages survive.

---

## Change 1 — Robust context-window default (BUG-2)

**Current.** `resolve_context_window(model)` (`llm/context_windows.py`) returns the
catalog/family window or `None`; on `None` the caller keeps `context_window_estimate`,
whose default `DEFAULT_CONTEXT_WINDOW_ESTIMATE = 12000` is a literal duplicated in
`config_sections.py:82`, `token_pressure.py:19`, `llm_step/build.py:56`. Unknown /
renamed / proxied model ⇒ silent 12K ⇒ compaction/pressure fire ~6% into a modern window.

**Change.**
- Introduce a single module-level `UNRESOLVED_MODEL_CONTEXT_WINDOW` in
  `llm/context_windows.py` and re-export the existing default from there; delete the
  three duplicated literals (import the constant).
- When a host has **not** set `context_window_estimate` and the model does **not**
  resolve, fall back to `UNRESOLVED_MODEL_CONTEXT_WINDOW` instead of 12000.
- Emit a runtime diagnostic (WARNING, redaction-safe) exactly once per run when the
  fallback is used, naming the unresolved model and urging an explicit
  `context_window_estimate` — so under-configuration is *loud*, not silent.
- A host-set value stays authoritative (unchanged) — the opt-out for small local models.

**Recommended value:** `UNRESOLVED_MODEL_CONTEXT_WINDOW = 128_000`. Rationale:
openclaude deliberately chose a 128k unresolved fallback after issue #635 ("a too-small
window makes auto-compact fire every turn"); hermes floors at 64k. Unknown ids are far
more likely modern large models than tiny ones, and "compact every turn" is the more
common, more damaging failure than the occasional overflow on an unconfigured small
model (which the loud diagnostic addresses). **Open decision — see sign-off.**

**Risk.** A genuinely small local model (e.g. 8k) that neither resolves nor is
configured now over-estimates ⇒ risk of context overflow instead of early compaction.
Mitigated by (a) the diagnostic and (b) the existing reactive context-overflow recovery
(`context_window_recovery.py`, `complete_request` CONTEXT_OVERFLOW retry).

## Change 2 — Window-fractional compaction caps (BUG-1, BUG-3, BUG-5)

**Current.**
- `_MAX_SCALED_COMPACTION_CHARS = 262_144` (`compaction_stage.py:37`) and
  `MAX_RUN_COMPACTION_CHARS = 262_144` (`contracts/context/run_budget.py:15`) cap the
  scaled budget as `min(scaled, max_chars, 262144)` — binds below `max_chars` on any
  model whose char budget exceeds 256K (BUG-1).
- On the `runner_defaults` path (no typed `RunContextBudget`), `resolve_run_context_budget`
  returns defaults verbatim, so `max_compaction_chars` stays 4000 and `_scaled_context_char_cap`
  returns the base unscaled ⇒ the llm-full excerpt is clipped to ~4000 chars regardless
  of the window (BUG-5).

**Change.**
- **Derive the compaction char budget from the resolved window on every path.** Make the
  default `ContextBudgetDefaults.max_compaction_chars` (and `max_chars`) a function of
  `context_window_estimate` (× chars/token × a documented fraction), so the
  `runner_defaults` path is window-scaled too — not a static 4000/6000. This closes BUG-5
  and BUG-3's "unscaled on the common path".
- **Replace the absolute 262144 ceiling with a window-relative one.** The summariser-input
  cap becomes `COMPACTION_INPUT_WINDOW_FRACTION × window_chars` (a documented cost-control
  fraction, default chosen so it does **not** bind for normal models, e.g. 0.6–0.8 of the
  input budget), instead of a fixed 256K. Keep a single hard-safety ceiling only as a
  memory backstop (large, clearly a backstop, commented), or drop it entirely if `max_chars`
  already bounds it. **Open decision — see sign-off.**
- Add a one-line rationale comment to each remaining constant (why this value, what it
  derives from).

**Risk.** Larger summariser input on big-context models ⇒ higher per-compaction aux cost.
Bounded by the fraction knob; measured via `evals/context_compaction_runner.py` before/after.

## Change 3 — Unify protected-message retention (BUG-4, data-loss)

**Current.** Two divergent predicates in `compaction_stage.py`:
- *Excerpt protection* (`~:665-673`) protects: `role==system` | last | `compaction_protected`
  | `compaction_evidence` | `material_fact_ids` | non-empty `material_unit_hashes`.
- *Post-summary retention* `_retained_messages_after_full_compaction` (`~:86-102`) keeps
  only: `system` | last | `compaction_protected` | `material_fact_ids`.
- A message flagged **solely** with `compaction_evidence` / `material_unit_hashes` is fed
  to the summariser then **dropped**, and the material-unit receipt mislabels it "compacted".

**Change.** Extract one `_is_protected_message(message, *, is_last)` predicate and use it
in **both** sites (and reuse in `_material_unit_receipt` classification). The unified set is
the *superset* — retention gains `compaction_evidence` + `material_unit_hashes`. Pure
correctness: evidence-flagged messages now survive the rewrite verbatim.

**Risk.** Slightly more messages retained post-compaction (by design). Negligible; the
protected set is host-controlled and already bounded by trimming.

---

## Public API / contract delta

- New public constant `UNRESOLVED_MODEL_CONTEXT_WINDOW` in `agent_driver.llm.context_windows`
  (additive). `DEFAULT_CONTEXT_WINDOW_ESTIMATE` single-sourced (behaviour of the *default*
  changes; the name stays).
- New documented config knobs (names TBD in impl) for the compaction char fraction; defaults
  chosen to preserve behaviour for already-well-configured hosts.
- No wire-contract (`AgentRunInput`/`AgentRunOutput`) field changes. No facade export removals.
- One new redaction-safe runtime diagnostic signal id (`context_window_unresolved_fallback`).

## SemVer & migration

**Minor** (`0.x`). Behaviour shifts only for hosts that (a) don't set
`context_window_estimate` and (b) run a model the catalog can't resolve — they move from an
assumed 12K to 128K window (compaction fires later, closer to reality). Migration note in
`CHANGELOG` + `docs/context-budget-and-rollback.md`: set `context_window_estimate` explicitly
for small/local models; the old behaviour is reproduced by setting it to `12000`.

## Test plan

- Unit: `resolve_context_window` unresolved → fallback value; host-set value authoritative;
  single-sourced constant. Fractional caps scale with window on the `runner_defaults` path
  (regression for BUG-5: assert llm-full excerpt is NOT clipped to 4000 on a 200K model).
  262144 no longer binds below `max_chars`.
- Unit: `_is_protected_message` parity — a `compaction_evidence`-only and a
  `material_unit_hashes`-only message survive `_retained_messages_after_full_compaction`
  (regression for BUG-4); receipt labels them retained.
- Behavioural: `evals/context_compaction_runner.py` before/after on the fake provider —
  compaction timing + token/cost delta; assert no protected-content loss.
- Diagnostic emitted once on unresolved fallback; redaction/bounds asserted.
- Full default suite green before close; ruff + pylint over changed modules.

## Out of scope (later phases / Option B)

- Live provider/model window probing (epic-017 phase D).
- Real tokenizer / per-language chars-per-token (BUG-6) — Option A phase 2.
- Token-aware honest `partial` + breaker (BUG-7), View/immutable-log + condenser pipeline,
  rolling summary, wiring `span_collapse`/`tool_clear` — Option B.
- Session-memory freshness-gate redesign.

## Open decisions for sign-off

1. `UNRESOLVED_MODEL_CONTEXT_WINDOW` value — **recommend 128_000** (openclaude-proven).
   Alternatives: 64_000 (hermes floor, safer for small models) / 200_000 (modern, riskier
   for small).
2. Summariser-input ceiling — **recommend** a window fraction that doesn't bind for normal
   models + drop the fixed 262144; alternative is to keep a large absolute backstop.
3. Should Change 1's louder behaviour be **opt-in** for one release (flag defaulting to old
   12K) or **default-on** with the diagnostic? Recommend default-on (the 12K default is the
   bug); the diagnostic + explicit-set opt-out cover the small-model case.
