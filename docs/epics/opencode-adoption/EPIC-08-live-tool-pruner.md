# EPIC-08 — Promote ToolResultPruner to a live default tier (S/M)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `prune` (Anthropic `clear_tool_uses`) is a **live, default-on** tier that clears
old tool-result content on context pressure — not a step buried behind an opt-in pipeline.

## The gap

`ToolResultPruner` (`context/compaction/condenser_tiers.py`, wrapping
`clear_old_tool_results`) already existed — but only as the cheapest tier **inside the
`CondenserPipeline`, which runs behind the opt-in `enable_compaction` flag**. The flagship
consumer (excel-ai) runs with `enable_compaction=False` (deterministic trim +
microcompaction), so the pruner **never fired on the path that actually runs** — the
highest-leverage compaction win was dormant. (See [[compaction-improvement-epic]].)

## What was built

A live, pressure-gated pruning pre-pass wired **independently of `enable_compaction`**,
next to the two pre-passes that already run that way (`enable_tool_arg_truncation`,
`enable_tool_history_compression`) in `apply_compaction_if_eligible`
(`runtime/single_agent/context_management/compaction_stage.py`):

- `_apply_live_tool_result_prune(host, context, request, token_pressure_state)` — under
  token pressure (`compact_recommended` / `blocking`) clears the CONTENT of OLD tool
  results (keeping the newest `keep_recent`) via `clear_old_tool_results`, in the
  **ephemeral request only**: `request.messages` is rewritten, the durable log is
  untouched, so nothing is permanently lost and a resume rebuilds the full history. It
  **commits only when it frees ≥ `live_tool_prune_min_chars`** — otherwise the request
  (and its prompt-cache prefix) is left byte-for-byte unchanged, so a negligible gain never
  churns the cache. Idempotent (an already-cleared result is skipped). Audit under the
  `live_tool_prune` metadata key.
- Config (`CompactionSettings` → delegating `RunnerConfig` properties):
  `live_tool_prune_enabled` (**default True**), `live_tool_prune_keep_recent` (3),
  `live_tool_prune_min_chars` (2000).

**Default-on but neutral until pressure.** A run that never reaches
`compact_recommended`/`blocking` is unaffected; under pressure the pruner shrinks the
ephemeral prompt exactly as opencode's default `prune` does — now on the deterministic-trim
path too, not only when the LLM-compaction pipeline is enabled.

Tests: `tests/runtime/test_live_tool_prune.py` (fires under pressure keeping recent;
no-op off-pressure; no-op below threshold; idempotent second pass; enabled by default).
Metadata inventory + full `tests/context` + `tests/runtime` sweeps green.

## Deliberately scoped

- **Recent-N protection, not a token-window + protected-tool-name set.** `keep_recent`
  (count-based) is the existing, tested protection in `clear_old_tool_results`; a
  token-window / protected-tool refinement can extend it later without changing the wiring.
- **Ephemeral only.** The prune never rewrites the durable message log — it shrinks what is
  *sent*, mirroring opencode's per-request `prune` and our stateless-provider pre-passes.
- **Complements, not replaces, the condenser tiers.** When `enable_compaction=True` the
  pipeline's own `ToolResultPruner` tier still runs; this live pre-pass covers the
  disabled-compaction path.
