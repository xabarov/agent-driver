# Compaction — research log

The epic's first phase. Goal: understand our current design, learn from prior art
(reference projects + external), and converge on a recommended direction before
writing code. Sections are filled as research proceeds.

## 1. Our current architecture (as-is)

Entry: `runtime/single_agent/context_management/compaction_stage.py::apply_compaction_if_eligible`
runs before final provider completion. Key modules:

- `context/compaction/eligibility.py` — when to compact.
- `context/compaction/orchestrator.py` — decide mode + attempt lifecycle + circuit breaker.
- `context/compaction/llm_full.py` — full LLM summary of a history excerpt.
- `context/compaction/session_memory_*.py` — session-memory extraction/store.
- `context/compaction/partial.py`, `span_collapse.py`, `tool_history.py`,
  `tool_clear.py`, `microcompaction.py` — partial strategies.
- `context/compaction/post_compact.py` — post-compaction cleanup/reinjection.
- `context/run_budget.py` + `contracts/context/run_budget.py` — the resolved
  context budget (`effective_context_budget`, `max_chars`, `max_compaction_chars`,
  `context_window_estimate`).

**Pipeline (per LLM step, before the provider call), `compaction_stage.py`:**
1. Cheap LLM-free pre-passes (flag-gated): tool-arg truncation, tool-history tiered
   compression — shrink in place, may relieve pressure before any summary.
2. Load session memory (artifact store, keyed by thread/run).
3. Eligibility + mode pick (`eligibility.decide_compaction`): `enable_compaction` →
   lock → circuit breaker → `token_pressure_state ∈ {compact_recommended, blocking}`
   → mode: SESSION_MEMORY (if enabled + memory present) → LLM_FULL → PARTIAL.
4. Attempt lifecycle: `start_attempt()` locks + mints `cmp_N`; emit STARTED.
5. Mode execution with fallthrough: session_memory → (non-fresh/empty) llm_full →
   partial. Each rewrites `request.messages` in place on success.
6. Post-compact cleanup (`post_compact.py`): re-inject steering state
   (planning_state, artifact_refs, rubric, recalled_memory) so it survives the rewrite.
7. Receipts/audit + emit COMPACTED{outcome}. Circuit breaker opens after
   `failure_limit=3` consecutive failures, `cooldown_attempts=3`, then a half-open probe.

**Modes:** *llm_full* — aux no-tool LLM call → structured `<persisted_summary>` (9
required keys; brittle parse); *session_memory* — deterministic LLM-free memory block
+ 6-msg tail, only when freshness=="fresh"; *partial* — lossy prefix bullet-stub
(≤10 lines × 160 chars) fallback; *span_collapse* / *tool_clear.idle_gap* — implemented
but **unwired** into dispatch; *tool_history* / *microcompaction* — pre-pass shrinks of
old tool/observation content (off by default / preview-capped).

**What is preserved:** metadata-driven. LLM-full *excerpt protection* keeps
role==system | last | `compaction_protected` | `compaction_evidence` |
`material_fact_ids` | non-empty `material_unit_hashes`. Trimming keeps
`protect_recent_turns=4` verbatim. **Mismatch (see BUG-4):** post-summary retention
is NARROWER than excerpt protection.

## 2. Tunables & magic-number inventory

The context budget **is** partially model-derived: `TrimmingSettings.resolved_for_model`
→ `llm/context_windows.resolve_context_window` (catalog + substring family: claude→200k,
gpt-5→400k, gemini→1M, floor 16k), but **only** when the host left the 12000 default
untouched *and* the model id resolves — otherwise 12000 silently persists (BUG-2). The
resolved budget lands in `context.metadata["effective_context_budget"]` and
`_scaled_context_char_cap` scales the base char caps up from it (capped at 262144, BUG-1).

Highest-signal knobs (full ~60-entry table lives in the survey; key ones):

| Knob | Value | Where | Should derive from |
|---|---|---|---|
| `DEFAULT_CONTEXT_WINDOW_ESTIMATE` | 12000 | config_sections.py:82 (+dup token_pressure:19, build:56) | model window |
| `_MAX_SCALED_COMPACTION_CHARS` / `MAX_RUN_COMPACTION_CHARS` | 262144 | compaction_stage:37, contracts/run_budget:15 | window |
| `ptl_retry_max_chars` | 4000 | config_sections:177 | window (only opportunistically scaled — BUG-5) |
| `tool_arg_truncation_max_chars` | 2000 | config_sections:180 | window |
| chars/token divisor | **4** (hardcoded) | run_budget:18/110, token_pressure:49, span_collapse, tool_history, microcompaction | tokenizer (BUG-6) |
| pressure ratios | 0.35 / 0.75 / 0.92 (config), 0.75/0.90/0.98 (run_budget) | config_sections:157-159, run_budget:162-164 | reconcile |
| `session_memory_stale_after_turns` | 4 | config_sections:175 | config (gates cheap mode — §6.5) |
| partial line/row caps | 160 chars × 10 rows | partial.py:91-92 | arbitrary (BUG-7) |
| `failure_limit` / `cooldown_attempts` | 3 / 3 | orchestrator:30-31 | config |
| tool_history tier breakpoints | 16k/32k/64k/128k/256k/500k | tool_history.py:44-62 | window ratios |

Two parallel char↔token conversions both hardcode 4 (`input_tokens*4` for max_chars;
`total_chars//4` for used tokens) — no tokenizer anywhere.

## 3. Reference projects

`reference/hermes-agent`, `reference/openclaude`, and `reference/openhands` (added
2026-08-06; OpenHands "condenser" survey pending — §3.3). The first two have **far
more elaborate** compaction than us, and several of their solutions map directly
onto our bugs.

**Model-window resolution (→ BUG-2).** Neither hardcodes a low estimate:
- hermes `agent/model_metadata.py::get_model_context_length()` — a **9-step
  resolution chain**: config override → endpoint metadata → **disk cache of probed
  windows** → static tables → **live `/models` probe** (Anthropic `/v1/models`,
  provider probes, models.dev) → family-pattern defaults → 256K final fallback,
  with `MINIMUM_CONTEXT_LENGTH = 64_000` invalidating suspiciously-small probes.
- openclaude `src/utils/context.ts::getContextWindowForModel()` — override →
  session override → provider runtime limits → **128k fallback (deliberately not
  8k — issue #635: a too-small window makes auto-compact fire every turn)** →
  `MODEL_CONTEXT_WINDOW_DEFAULT = 200_000`. Their *default* is 200K; ours is 12K.
- Lesson: derive the window live from the provider/model + cache, with a modern
  floor. Our static 12K is the single worst gap.

**Reserving summary-output budget (→ BUG-1 / "fires every turn").** openclaude
`getEffectiveContextWindowSize()` = window − `MAX_OUTPUT_TOKENS_FOR_SUMMARY`
(20K, sized from the **p99.99 of real compact-summary output = 17,387 tokens**),
floored non-negative. Threshold derived from telemetry, not a guess.

**Cost-ordered layered pipeline.** openclaude applies cheapest-first so a cheap
layer can make the LLM summary a no-op: (1) **snipCompact** — the model marks
messages for removal by short ID; (2) **microCompact** — deterministic no-LLM
clearing of old tool outputs (`Read`/shell/`Grep`/`WebFetch`/`mcp__*` …) →
`[Old tool result content cleared]`; (3) collapse paths; (4) **autoCompact** — full
LLM summary, preceded by `pruneByRelevance` (keeps recent `compactTailTurns=3`,
force-preserves tools+errors); (5) **sessionMemoryCompact** tried first inside it.
Large tool outputs are **offloaded to disk** (`toolResultStorage`, `<persisted-output>`
pointer) rather than truncated. hermes mirrors this with `prune_tool_results_only()`
as a cheap pre-pass before hierarchical summarization.

**Other adoptable ideas (ranked):**
1. Live-probe + cache the context window (hermes) — root fix for BUG-2.
2. Threshold = window − telemetry-sized reserved output, floored (openclaude).
3. Cost-ordered pipeline: deterministic tool-clear + model-directed snip before
   LLM summary (both).
4. **Token-budget tail** (`_find_tail_cut_by_tokens`, ~20K) instead of a fixed
   message count — robust to varying message sizes (hermes).
5. **Iterative/running summary**: update the prior summary rather than re-summarize
   from scratch each pass (hermes).
6. **Partial compaction keeping the tail verbatim + cache-friendly ordering**
   (summary precedes kept tail) (openclaude).
7. **Anti-thrashing guard with a user-visible reason**: if the last two passes
   saved <10%, stop and say why instead of silent re-loop (hermes `should_compress_info`).
8. **Summarizer feasibility probe**: verify the aux model's own window can hold the
   main model's compaction threshold; auto-lower otherwise (hermes).
9. **Incremental prefix-hashed token counter** to avoid re-tokenizing every turn
   (openclaude `IncrementalTokenCounter`).
10. **Preserve the full pre-compaction transcript** + point the model at it
    (hermes session-split / openclaude continuation message).
11. **Prompt-cache-break awareness**: reset the cache-read baseline on compaction
    so the post-compact drop isn't mis-flagged (openclaude).

**Where we could differentiate:** neither does semantic/embedding-based importance
scoring — pruning is recency + role + keyword heuristics. A material-unit /
evidence-aware importance model (we already track material_fact_ids / material unit
hashes) could beat keyword overlap.

**Caveats:** openclaude leans on feature flags (several paths dead-code-eliminated
in external builds); hermes runtime compaction is a 6.5k-line class entangled with
SQLite session rotation — learn the ideas, don't lift wholesale.

## 4. External prior art (surveyed 2026-08)

**Anthropic (Claude platform) — closest analogue to our design.**
- *Context editing* (`clear_tool_uses_20250919`): server-side clearing of old
  tool-use/result pairs + thinking blocks once context passes a threshold —
  described as the "safest, lightest-touch" compaction. Runs before token
  counting and after prompt-cache lookup. We have analogues (`tool_clear`,
  `tool_history`) but run them client-side.
- *Compaction*: server-side full-conversation summary near the window limit —
  analogous to our `llm_full`.
- *Memory tool*: the agent writes notes persisted OUTSIDE the window and pulls
  them back later — an agentic version of our `session_memory`.
- Reported gains: context-editing alone +29%, +memory +39% over baseline; a
  100-turn web-search eval cut tokens 84% and completed runs that otherwise
  exhausted context. Anthropic recommends server-side compaction over SDK-side.
- "Protect more context" (Claude Code): protecting recent/important context
  improved quality — validates our protected-turn policy; suggests leaning into
  protection + cheap tool-result clearing *before* full summarization.

**LangGraph / LangMem.** Running-summary node fired conditionally on a token/
message threshold, replacing old messages with a rolling summary
(`SummarizationNode` / `summarize_messages`); `trim_messages` for hard trims.
LangMem extracts facts/behaviours to a long-term store. Framing: "the context
window is the only reality — memory's job is to decide what goes in, when, and in
what form."

**MemGPT / Letta.** Tiered memory (in-context vs external) with self-editing and
paging between tiers.

**Deriving the model window (directly addresses BUG-2).** LiteLLM exposes
`get_max_tokens()` and a model catalog (`context_window` / `max_input_tokens` /
`max_output_tokens`). The window can be *derived from a model registry / provider*
rather than a static 12K estimate.

**Adoptable ideas (ranked):**
1. **Derive the model window from the provider/registry** (LiteLLM-style), config
   override next, a *modern* floor last — fixes BUG-2 at the root instead of a
   blind 12K default.
2. **Express caps as fractions of the resolved window**, not absolute char magic
   numbers — fixes BUG-1/BUG-3 (`_MAX_SCALED_COMPACTION_CHARS`, base caps).
3. **Cheap tool-result clearing as an explicit FIRST tier** before LLM summary
   (Anthropic's "lightest touch"); we have the pieces — make it the default first
   tier and measure token/quality impact.
4. **Agentic memory (write-notes-outside-window)** as a complement to
   `session_memory` for material that must survive summarization.
5. **Incremental/rolling summary** (update a running summary) instead of
   re-summarising the full excerpt each time — cheaper on repeated compaction.

Sources: Anthropic [context management](https://claude.com/blog/context-management),
[context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing),
[memory tool](https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool),
[effective context engineering](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents);
[LangMem summarization](https://langchain-ai.github.io/langmem/guides/summarization/);
[lang-memgpt](https://github.com/langchain-ai/lang-memgpt);
[LiteLLM token usage](https://docs.litellm.ai/docs/completion/token_usage),
[LiteLLM model catalog](https://github.com/BerriAI/litellm/discussions/21029).

## 5. Design options & recommendation

The research converges on a clear ordering: **budget-correctness is the foundation**
(most bugs are "the runtime doesn't know the real window, so every downstream cap is
wrong"), the **layered pipeline** is the quality/cost lever, and **agentic memory** is
a later bet.

### Cross-cutting principles (from prior art)
- The context window is the only reality; derive it, don't guess. Robust resolution
  chain: **provider/model probe** (litellm `get_max_tokens` / `model_info.context_window`)
  → catalog → host config → **modern floor** (refs use 64K–200K, never ~12K). Cache it.
- Express every cap as a **fraction of the resolved window** (+ a telemetry-sized
  reserved summary-output budget, floored non-negative), not an absolute char literal.
- **Cost-ordered pipeline**, cheapest first, each tier able to short-circuit the next:
  deterministic tool-result clearing → keep-tail partial → LLM summary → memory.
- **Never silently destroy protected/evidence content**; never report `success` on a
  lossy fallback that didn't actually fit.

### Option A — Budget correctness (bug-fix foundation) — RECOMMENDED FIRST
Fix the window-derivation chain (BUG-2/5), make all caps window-fractional and kill the
262144 / 4000 magic numbers (BUG-1/3), **unify the two protection sets** (BUG-4, the
data-loss bug), and replace chars/4 with real token counts where the provider gives them
(BUG-6). Mostly correctness; default behaviour *improves* (compaction fires at the right
time, keeps what it was told to keep). Lowest risk, highest certainty. SemVer: minor
(behaviour shifts for under-configured hosts — document + provide an opt-out to the old
static estimate).

### Option B — Layered compaction pipeline (quality/cost) — SECOND
On top of A, restructure the mode dispatch into an explicit **cost-ordered tier
pipeline** (tool-result clear → keep-tail partial with cache-friendly ordering →
llm-summary with token-budget tail + iterative/running summary → session-memory),
wire the currently-dead `span_collapse`/`tool_clear` primitives in, and make `partial`
token-aware + honest (BUG-7). Add the anti-thrashing "ineffective, here's why" outcome
and the summarizer-feasibility probe. This is where the Anthropic/openclaude/hermes
"cheap tiers first, summary last" gains live (ref: 84% token cut). Bigger surface;
land tier-by-tier behind flags, measured via the eval compaction runner.

### Option C — Agentic / tiered memory (bet) — DEFER
A memory-tool that lets the agent write notes outside the window and pull them back
(Anthropic memory tool / MemGPT tiers), complementing `session_memory`. Highest upside
for very long runs but the most speculative; evaluate after A+B with real trajectories.

### Recommendation
Sequence **A → B**, defer **C**. Start A with the window-derivation chain (unblocks all
fractional caps) and the BUG-4 retention unification (correctness/data-loss). Keep each
increment small and test-gated (unit + `evals/context_compaction_runner.py` + full
suite); keep default behaviour green except the documented, opt-out-able window-default
change. The OpenHands condenser survey (§3.3, pending) should inform the Option-B tier
abstraction (its `Condenser` composition + amortized/rolling summary are the closest
prior art to the pipeline we'd build).
