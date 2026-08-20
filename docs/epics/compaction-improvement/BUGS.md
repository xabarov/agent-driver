# Compaction — known bugs & smells

Recorded during the 2026-08-06 review that triggered this epic. Each is a
candidate to resolve during implementation; the research phase should decide the
*right* fix (not just paper over the number).

## BUG-1 — `_MAX_SCALED_COMPACTION_CHARS = 262_144` is a model-blind ceiling

- **Where:** `agent_driver/runtime/single_agent/context_management/compaction_stage.py`
  (module constant; used only in `_scaled_context_char_cap`).
- **What:** the scaled compaction char budget is `min(scaled, max_chars, 262144)`.
  `max_chars` is derived from the resolved context budget (model-aware), but the
  hardcoded 256K-char (~65K-token) ceiling **binds below it** on large-context
  models. Per `docs/context-budget-and-rollback.md`, a 210K-token model resolves
  `max_chars≈720000`; the summariser excerpt is then clamped to 256K chars
  regardless.
- **Why it matters:** on modern ~200K–1M-token models the compaction excerpt is
  artificially throttled to a fraction of the window. If the intent is a
  cost-control cap on summariser *input*, that intent is undocumented and not
  expressed relative to the model.
- **Open question for research:** should the summariser-input cap be (a) removed
  and left to `max_chars`, (b) a fraction of the resolved window, or (c) a
  documented, config-driven absolute cost cap? Decide from cost/quality data.

## BUG-2 — `DEFAULT_CONTEXT_WINDOW_ESTIMATE = 12000` fallback is far below modern windows

- **Status: FIXED.** Two parts, both closed:
  1. *Robust resolution + modern fallback* (core, commit `c19ddff` and follow-ups):
     `agent_driver/llm/context_windows.py` resolves the REAL window per model id
     (catalog → family table → `None`) with a `MIN_RESOLVED_CONTEXT_WINDOW` floor;
     `TrimmingSettings.resolved_for_model` uses the resolved window, and for an
     **unresolved/renamed/proxied id** now falls back to the modern
     `UNRESOLVED_MODEL_CONTEXT_WINDOW = 128_000` (source `unresolved_fallback`, a
     runtime diagnostic fires) instead of silently keeping the legacy 12k. Tested in
     `tests/llm/test_context_windows.py::test_unknown_model_falls_back_to_modern_window`
     (+ catalog/family/floor/explicit-wins cases).
  2. *Single-source the constant* (sub-item c, 2026-08-20): the pre-resolution
     `12000` default was a bare literal duplicated in `config_sections.py`,
     `context/token_pressure.py`, and `llm_step/build.py`. It now lives once as
     `DEFAULT_CONTEXT_WINDOW_ESTIMATE` in `agent_driver/context/token_estimation.py`
     (a cycle-free leaf both consumers already import) and the three field defaults
     read from it; documented as the fail-safe that applies only before per-model
     resolution. Drift guard: `test_default_window_estimate_is_single_sourced`.
- **Where:** `agent_driver/runtime/single_agent/lifecycle/config_sections.py:82`
  (duplicated as literals in `agent_driver/context/token_pressure.py:19` and
  `agent_driver/runtime/single_agent/llm_step/build.py:56`).
- **What:** when a host does not set `context_window_estimate`, the runtime assumes
  a **12K-token** window. Token-pressure and compaction eligibility then fire far
  too early (the runtime thinks the window is ~6% of a 200K model's).
- **Why it matters:** silent under-configuration degrades behaviour dramatically
  and invisibly. The value is also a magic literal repeated in ≥3 places.
- **Open questions for research:** (a) is a low fail-safe default correct, or should
  the default track a modern floor? (b) should the runtime *derive* the window from
  the provider/model when available instead of a static estimate? (c) at minimum,
  single-source the constant and document "host must override for its model."

Note on BUG-2: the architecture survey (RESEARCH §2) found the window **is**
partially model-derived — `TrimmingSettings.resolved_for_model` →
`resolve_context_window` (substring family match) — but only when the host left the
12000 default untouched *and* the model id resolves; on an unknown/renamed/proxied
model id it **silently keeps 12000**. So BUG-2 is really "silent low fallback on
unresolved models", and the fix is a robust resolution chain (live probe + modern
floor), not merely bumping a literal.

## BUG-3 — inconsistent pressure-threshold ratios across paths (needs a design decision)

- **Status: FIXED (design decided + implemented, commit `888ba6b`; char-cap rationale
  documented 2026-08-20).** Decision (see
  [`DESIGN-bug3-pressure-ratios.md`](DESIGN-bug3-pressure-ratios.md), user sign-off
  `compact_ratio = 0.75`): give `compact_recommended` a window-relative safety-net so
  compaction fires at a consistent window point regardless of path, WITHOUT unifying
  the absolute-threshold formulas (the ratio nets are authoritative). `TokenPressureInput`
  gained `compact_ratio: float = 0.75` (slotted between delegate 0.45 and blocking 0.92);
  `_pressure_state`'s compact branch is now `used ≥ compact_threshold OR ratio ≥
  compact_ratio`; the snapshot carries `compact_ratio`. Default path unchanged
  (0.75·window); typed-budget path now compacts at ~0.75·window instead of ~0.90.
  Regression tests: `tests/context/test_token_pressure.py` —
  `test_compact_ratio_net_triggers_compaction_below_absolute_threshold`,
  `test_pressure_ratio_ladder_is_ordered`. The "Also:" note below (base char caps lacked
  a rationale) is closed: `ptl_retry_max_chars` / `tool_arg_truncation_max_chars` now
  document that they are conservative static FLOORS the window-relative cap scales up
  from (BUG-1/BUG-5), binding only when the window is unknown.

Refined finding (2026-08-06): there are **three** threshold-ratio triples with
**different bases**, so pressure/compaction fires at different points depending on
which path a run takes:
- `config_sections.for_context_window` (default path): `0.35 / 0.75 / 0.92 × WINDOW`
  → warning/compact/blocking (config_sections.py:163-165).
- `context/run_budget.resolve_run_context_budget` (typed path): `0.75 / 0.90 / 0.98 ×
  INPUT_TOKENS` (run_budget.py:169-171).
- `context/token_pressure` decision ratios: `early=0.35 / delegate=0.45 / blocking=0.92`
  (token_pressure.py:24-26).
Within one run it is self-consistent, but a host that sets a typed budget with
`input_tokens ≈ window` warns at 0.75·window vs a default-path host at 0.35·window —
very different behaviour, undocumented. **This is a design decision (what should the
thresholds mean, off which base), not a mechanical fix** — do it as its own scoped
increment with a documented rationale, not a blind number change.

Also: the base char caps (`ptl_retry_max_chars=4000`, `tool_arg_truncation_max_chars=2000`)
lack a documented cost/quality rationale for their absolute values.

## BUG-4 — retention-policy mismatch drops evidence-flagged messages (DATA LOSS)

- **Status: FIXED (Option A phase-1, commit `c19ddff`; receipt half locked
  2026-08-20).** The excerpt protection and the post-summary retention set now share
  **one** predicate, `_is_protected_message`, which honours all four host flags
  (`compaction_protected`, `compaction_evidence`, `material_fact_ids`,
  `material_unit_hashes`) plus system/last-message — so a message flagged *solely*
  with evidence/unit-hashes is retained, not dropped. Because it is retained, its
  hashes land in the receipt's `retained_unit_hashes`, never mislabelled
  `compacted`. Regression tests: `tests/runtime/test_compaction_budget_correctness.py`
  — `test_evidence_only_message_is_protected`,
  `test_material_unit_hashes_only_message_is_protected`,
  `test_retention_keeps_evidence_and_material_hash_messages`, and (receipt half, new)
  `test_receipt_labels_protected_material_hash_as_retained_not_compacted` +
  `test_receipt_omits_dropped_hashes_when_leading_groups_pre_dropped`.
- **Where:** `compaction_stage.py` — LLM-full *excerpt protection* (`~:665-673`) vs
  *post-summary retention* `_retained_messages_after_full_compaction` (`~:86-102`).
- **What:** excerpt protection honours `compaction_evidence` and
  `material_unit_hashes`; post-summary retention only checks `compaction_protected`
  and `material_fact_ids`. A message flagged **solely** with `compaction_evidence` /
  `material_unit_hashes` is fed to the summariser but then **dropped from the final
  message list** — and the material-unit receipt mislabels those hashes "compacted".
  Its content survives only if the summary happened to capture it.
- **Why it matters:** silent loss of exactly the material the host marked as evidence
  to protect. Highest-severity correctness bug found. The two protection sets must
  be unified.

## BUG-5 — LLM-full excerpt hard-clipped to ~4000 chars on the common path

- **Where:** `run_budget.py::resolve_run_context_budget` (`~:66`) + `_scaled_context_char_cap`.
- **What:** when the caller supplies no typed `RunContextBudget` (the common
  `runner_defaults` path), `max_compaction_chars` stays at the 4000 default and the
  scaler returns the base **unscaled**. The LLM-full history excerpt is then clipped
  to ~4000 chars **regardless of a 200K-token model** — most history is dropped
  (with only a sha256 receipt) before the summariser sees it.
- **Why it matters:** on the default path, "full" compaction summarises a sliver of
  history. Arguably more impactful than BUG-1. Fix couples with the window-derivation
  work (BUG-2).

## BUG-6 — chars/token = 4 hardcoded everywhere, no tokenizer

- **Where:** `run_budget.py:18/110`, `token_pressure.py:49`, `span_collapse.py`,
  `tool_history.py`, `microcompaction.py` (`_CHARS_PER_TOKEN=4` / `//4` / `*4`).
- **What:** both char↔token conversions assume 4 chars/token with no tokenizer. For
  CJK/RU or code-heavy content this mis-estimates badly, so pressure thresholds and
  char budgets fire at the wrong time (same class as the MeetScript RU incidents).
- **Why it matters:** the trigger and the budgets are systematically wrong for
  non-English/code content. Research: provider token counts / a real tokenizer /
  per-language ratio.

## BUG-7 — partial compaction is lossy, not token-aware, and reports success

- **Status: FIXED (2026-08-19, compaction hardening C1).** `_apply_partial_compaction`
  now measures `chars_freed` and reports `successful` only on real progress;
  a no-op or no-shrink attempt leaves the View untouched and records an honest
  `skipped` (`skip_reason: no_op | insufficient_progress`) via
  `complete_attempt(result=None)` — neutral, so the circuit breaker is neither
  falsely reset nor unfairly advanced. `chars_freed` is now on the durable
  `MEMORY_COMPACTED` payload. Note: the tracing done for this fix established that
  compaction is **non-destructive** — the reduction lands only in a throwaway
  per-step `request.messages`; the raw log survives in `protocol_messages` — so the
  original "context destroyed" framing overstated the loss; the real residual bug
  was the false breaker reset. Deeper token-aware budgeting lands with the Condenser
  pipeline cutover (C2). Tests: `tests/runtime/test_compaction_partial_honesty.py`.
- **Where:** `partial.py:91-92` (`content[:160]`, ≤10 rows).
- **What:** the ultimate fallback (used after llm_full failures too) can replace a
  large prefix with a few hundred chars of bullet stubs with **no size accounting**,
  yet returns `success=True` — which **resets the circuit breaker**, masking that
  context was destroyed rather than summarised.
- **Why it matters:** hides irrecoverable context loss and defeats the breaker's
  purpose. Needs token-aware budgeting + an honest "insufficient" outcome.

## Smaller issues

- `span_collapse` and `tool_clear.idle_gap_exceeded` are implemented but **unwired**
  into the orchestrator dispatch (dead/available primitives).
- **session-memory freshness gate** (`stale_after_turns=4`) means the cheap LLM-free
  mode rarely fires under real pressure → falls through to expensive `llm_full`
  almost always (design weakness, not strictly a bug).
- Failed aux-compaction calls are **billed to the cost ledger**
  (`_account_compaction_cost`) — a flapping summary provider both opens the breaker
  and accrues spend.

_(Add further bugs/smells uncovered during the research phase below.)_
