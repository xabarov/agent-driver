# Compaction Improvement

Status: research-first. Predecessor: current `main` (0.10.x).

A single-topic epic to raise the quality of Agent Driver's context-compaction
subsystem. Unlike a mechanical refactor, this epic **starts with research** — our
own code, reference projects, external prior art, and deliberate design thinking —
and only then commits to an implementation. The trigger was a review that surfaced
context-window magic numbers not tied to the model's real window (see
[`BUGS.md`](BUGS.md)); the broader goal is a compaction plane that is correct,
model-aware, observable, and cheap.

## Why this epic exists

Compaction decides what the agent *remembers* under token pressure. Done well it
preserves task-critical material at low cost; done poorly it either fires too
early (wasting tokens/quality on tiny contexts) or too late (context-overflow
failures), or throws away material the run still needs. Today the subsystem works
but carries:

- **Model-blind constants** — a hardcoded 256K-char scaled ceiling and a 12K-token
  fallback window that predate ~200K-token models (see `BUGS.md`).
- **God-functions** in `runtime/single_agent/context_management/compaction_stage.py`
  (a separate cautious safe-extract refactor is already improving these).
- Unproven assumptions about *what* to keep (protected turns, material units,
  evidence) vs *how* to summarize (llm-full, session-memory, partial).

## Non-goals

- Not a rewrite of the agent loop or governance. Compaction stays a pre-completion
  stage the runtime owns.
- No domain-specific memory policy — that stays in the consumer (excel-ai).
- No benchmark-fitting. Improve via the harness levers (budget model, decision
  policy, summarization quality, observability), per repo `CLAUDE.md`.

## Phases

1. **Research (this epic starts here).** See [`RESEARCH.md`](RESEARCH.md).
   - Map our current compaction architecture end-to-end (eligibility → decision →
     llm-full / session-memory / partial → post-compact cleanup → receipts).
   - Inventory every tunable + magic number and where it *should* come from
     (model window, config, runtime signal).
   - Study reference projects (`reference/hermes-agent`, `reference/openclaude`,
     `reference/openhands`) and external prior art (context management /
     summarization / memory in agent frameworks). Google + read, capture ideas.
   - Produce a short design-options memo with a recommended direction.
2. **Design decision.** Record the chosen approach (contracts/knobs/behavior delta,
   SemVer class, migration) before editing — see the execution-backend
   `EPIC_STANDARD.md` conventions for the bar.
3. **Implementation.** Land in small, test-gated increments; keep default behavior
   green unless a documented, opt-in change is agreed.
   - **Active slice — compaction hardening C1–C3** (2026-08-19), informed by the
     `reference/deepseek-harness` survey (memory `deepseek-harness-survey`). See
     [`DESIGN-deepseek-C1-C3.md`](DESIGN-deepseek-C1-C3.md). Sequenced **C3 → C1 → C2**:
     - **C3** (S) — cached derived-View + O(1) next-seq; fixes the `_next_seq` O(n²)
       RAM/CPU blowup *root* that `CLAUDE.md` currently only backstops.
     - **C1** (M) — journaled non-destructive compaction (shadow-not-mutate log events);
       fixes BUG-7 (lossy partial reports success), adds audit/replay + orphaned-lock
       crash detection.
     - **C2** (M) — wire the already-built-but-dormant `condenser.py` pipeline as the
       compaction seam (Option-B1b cutover), behind an opt-in flag, emitting C1's facts.

## Documents

- [`BUGS.md`](BUGS.md) — concrete defects/smells found (seed: the magic numbers).
- [`RESEARCH.md`](RESEARCH.md) — the research log + findings + design options.
- [`DESIGN-deepseek-C1-C3.md`](DESIGN-deepseek-C1-C3.md) — active implementation slice
  (C3 cached fold + O(1) seq, C1 journaled shadow compaction, C2 Condenser-pipeline
  cutover), grounded in the deepseek-harness comparison.
