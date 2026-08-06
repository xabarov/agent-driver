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

## BUG-3 — base char caps not single-sourced / undocumented rationale

`ptl_retry_max_chars=4000`, `tool_arg_truncation_max_chars=2000`,
`max_compaction_chars=4000` are scaled via `_scaled_context_char_cap`, but their base
values + the scaling formula (and the 0.35/0.75/0.92 vs 0.75/0.90/0.98 ratio
mismatch between `config_sections` and `run_budget`) lack a documented cost/quality
rationale. Reconcile + document.

## BUG-4 — retention-policy mismatch drops evidence-flagged messages (DATA LOSS)

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
