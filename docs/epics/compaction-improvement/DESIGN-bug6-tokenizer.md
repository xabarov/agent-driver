# Design Decision — BUG-6: real token counting (kill hardcoded chars/token=4)

Status: **decided + phase-1 implemented** (2026-08-06). Branch: `epic/compaction-bug6`.
Predecessor: Option A phase-1/1b + typed-ceiling + BUG-3 (all merged).

**Sign-off (user):** bounded EMA (0.7 prior / 0.3 observed, clamp [2, 8]); phase 1 =
trigger + budget only (defer `TokenCounter` protocol + display sites to phase 2).
**Phase-1 implemented:** shared `context/token_estimation.py` (`estimate_tokens` /
`chars_for_tokens` / `calibrate_chars_per_token`); `token_pressure` uses a `chars_per_token`
field; `run_budget.resolve_run_context_budget` takes `chars_per_token`; `request.py` reads
the per-run calibrated `context_chars_per_token` from metadata and threads it into both;
`llm_step` updates the EMA from `_usage.input_tokens` after each response. Blast radius: **zero**
(the fake provider reports input_tokens = chars//4, so calibration is a no-op in tests but
active with real providers). Tests: `tests/context/test_token_estimation.py`.
**Phase-2 (later):** the optional `TokenCounter` protocol + fold the display/accounting sites
(`breakdown`, `tool_history`, `span_collapse`, `microcompaction`, `batch/compress`) onto the
shared estimator.

## Finding

`used_tokens ≈ chars // 4` (and `chars = tokens × 4`) is hardcoded in ~8 places, each
with its own local constant, and drives BOTH the compaction/pressure **trigger** and the
budget **char↔token** conversions:
- `context/token_pressure.py:55` — `used_tokens_estimate = total_chars // 4` (the trigger).
- `context/run_budget.py` — `_ESTIMATED_CHARS_PER_TOKEN = 4` (input_tokens ↔ max_chars).
- `context/breakdown.py`, `context/compaction/tool_history.py`, `span_collapse.py`,
  `microcompaction.py`, `batch/compress.py` — display / accounting estimates.

4 chars/token is an English-prose average; for CJK/RU it under-counts and for code it can
over-count, so the trigger fires at the wrong time on non-English/code content (the
MeetScript RU incident class). There is **no tokenizer or `TokenCounter` abstraction** in
the codebase today.

## Constraints (repo `CLAUDE.md`)

Domain-neutral runtime; no heavy default dependency; **no network in the runtime by
default** (same rule that deferred live window probing). So the default path must not
require tiktoken/HF or a network call.

## Key asset already present

After every provider response the runtime already has the **actual** input token count
(`_usage.input_tokens`, folded into the cost ledger at `llm_step/__init__.py:~513`). We
also know the chars we sent. So the true `chars/token` ratio for THIS model+content is
observable at zero cost — we just are not using it.

## The decision — calibrate from real usage, with an optional exact counter

Three layers, cheapest/default first:

1. **Single-source the estimator.** One `estimate_tokens(text_or_chars, *, ratio)` +
   `DEFAULT_CHARS_PER_TOKEN = 4` (a shared module, e.g. `context/token_estimation.py`).
   Replace the ~8 local `4`s with it. Behaviour-neutral on its own.
2. **Calibrate the ratio from provider usage (the core fix).** After each response,
   compute `observed = chars_sent / max(1, actual_input_tokens)`, fold it into a bounded
   EMA stored in run metadata (`context_chars_per_token`), and feed THAT ratio into the
   next preflight `estimate_token_pressure` + budget conversions. Clamp to a sane range
   so a bad datapoint can't wreck the estimate. Self-correcting per model+content;
   dep-free; network-free. (This is what hermes' `update_from_response` does.)
3. **Optional pluggable `TokenCounter` protocol.** A host may inject an exact counter
   (tiktoken/HF/provider count-tokens endpoint); default is the calibrated estimator.
   Opt-in, so the default stays dependency-free.

The calibrated ratio is authoritative for the **trigger and budget** (where accuracy
changes behaviour); the display-only sites can adopt the shared estimator without the
calibration loop.

## Phasing (small, test-gated increments)

- **Phase 1 (this design):** shared estimator + the calibration loop on the pressure +
  budget path (`token_pressure`, `run_budget`, the compaction char budget). Capture
  `chars_sent`/`input_tokens` post-response; maintain the EMA; consume it in the next
  preflight. Metadata: `context_chars_per_token` (+ document the key). This is where the
  win is — the trigger and budget become content-accurate over a run.
- **Phase 2 (later):** the `TokenCounter` protocol + fold the display/accounting sites
  (`breakdown`, `tool_history`, `span_collapse`, `microcompaction`, `batch/compress`)
  onto the shared estimator.

## Behaviour / SemVer

**Minor.** The first turn of a run still uses the 4.0 default (no usage yet); subsequent
turns use the calibrated ratio, so on RU/CJK/code the trigger fires closer to the real
token count (earlier for dense content, later for sparse). No public wire-contract change;
one new metadata key (`context_chars_per_token`) and, in phase 2, one optional public
protocol.

## Test plan

- Unit: `estimate_tokens` single-source parity with the old `// 4` on English; the EMA
  calibrator moves the ratio toward an observed value and stays within clamp bounds; a
  degenerate `input_tokens=0`/missing usage is ignored (keeps the prior ratio).
- Behavioural: a run whose observed ratio is ~2 (CJK-like) reaches `compact_recommended`
  at roughly half the chars of a 4.0 run — an eligibility regression.
- The calibrated ratio round-trips through run metadata across turns.
- Full suite; `evals/context_compaction_runner.py` before/after.

## Open decisions for sign-off

1. Calibration smoothing — **recommend a bounded EMA** (e.g. `ratio = 0.7·ratio + 0.3·observed`,
   clamped to `[2.0, 8.0]`) over last-value (EMA resists per-turn noise). Confirm the clamp
   range + EMA weight.
2. Scope of phase 1 — **recommend trigger+budget only** (defer the display sites +
   `TokenCounter` protocol to phase 2). Confirm.
3. Should the `TokenCounter` protocol land in phase 1 (public surface now) or phase 2?
   **Recommend phase 2** (keep phase 1 dependency-free and internal).
