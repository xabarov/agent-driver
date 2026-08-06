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

## BUG-3 — (placeholder) tunables not single-sourced / undocumented rationale

- Several char caps (`ptl_retry_max_chars=4000`, `tool_arg_truncation_max_chars=2000`,
  `max_compaction_chars=4000`) are scaled at runtime via `_scaled_context_char_cap`
  (good), but their *base* values and the scaling formula lack a documented
  rationale tying them to observed cost/quality. Research to confirm whether the
  scaling math is sound across the model-window range.

_(Add further bugs/smells uncovered during the research phase below.)_
