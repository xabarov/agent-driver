# Improvement Plan E1–E8 (cross-harness, post review-cycle-2)

Status: planning / backlog. Date: 2026-06-09.

Purpose: track the remaining high-value capabilities surfaced by a comprehensive
comparison of `agent-driver` against three reference harnesses — NousResearch
**hermes-agent**, Gitlawb **openclaude**, and LangChain **deepagents**. Review
cycle 2 (N1–N6, D2–D5) is done; this backlog is the *next* layer, after
deduplicating everything we already ship.

How to read this: each item has a checkbox, the rationale (**Why**), where it
lands in our code (**Where**), the reference it draws from (**Ref**), effort,
dependencies, and an acceptance note (**Done when**). Items are ordered by the
recommended implementation sequence; see [Sequencing](#sequencing).

Companion doc: [Testing Plan](testing-plan-2026-06-09.md) — how each item is
validated and the budget for live runs.

## Already shipped (do NOT re-propose)

For reference, the reference harnesses' headline features that agent-driver
**already** implements (so they are out of scope here):

- hook-chains + dedup/cooldown/field-filters (openclaude `utils/hookChains.ts`) →
  `runtime/hook_chains.py`
- rubric / grader goal-gate (deepagents `middleware/rubric.py`) →
  `runtime/single_agent/lifecycle/rubric_hook.py`
- Anthropic prompt-cache breakpoints (deepagents `graph.py`) →
  `llm/providers_impl/anthropic.py` + `enable_prompt_cache`
- harness profiles (deepagents `profiles/harness/…`) → `harness/`,
  `contracts/profiles.py`
- trajectory compression (hermes `trajectory_compressor.py`) →
  `batch/compress.py`
- provider error classification + failover (hermes `error_classifier.py`) →
  `llm/error_classifier.py`, `llm/router.py`
- cost ledger + budget enforcement (hermes `usage_pricing.py`, openclaude
  `cost-tracker.ts`) → `observability/cost_ledger.py`
- MCP client + server, scheduler/cron, gateway submit/respond, subagents +
  mailbox/merge, skills + frontmatter, tool-result spill, compaction tiers.

---

## Backlog

### E1 — Auxiliary / cheap-model routing  ·  High · Med  ·  **DONE 2026-06-09**

- [x] Auxiliary-model seam: `RunnerConfig.auxiliary_provider` +
      `auxiliary_model`; the full-compaction side task routes to them when set,
      else falls back to the main provider + `compaction_model`. Provider and
      model resolve independently (either can be overridden).
- [x] Wire into compaction (`context_management/compaction_stage.py` →
      `run_full_llm_compaction`).
- [x] Account the compaction call's usage in the run cost ledger, tagged by the
      compaction model's name — so auxiliary spend is separated in the rollup
      (`_account_compaction_cost`).
- [x] Tests: cost-accounting + model tag, no-op without tokens, and end-to-end
      routing to the auxiliary provider via the runner.

Notes: session-memory extraction is deterministic (no LLM call), so it needs no
routing. Rubric grading uses a host-supplied `GradeFn`, so the host picks the
grader model directly — auxiliary routing there is the caller's choice, not a
runtime config (documented, intentionally not forced).

**Why:** biggest cost lever — these side tasks are frequent and don't need a
frontier model; pairs directly with our cost-ledger + rubric + compaction.
**Where:** `llm/` (router seam) + the three call sites above.
**Ref:** hermes `/agent/auxiliary_client.py`, `/agent/context_compressor.py`
(auto-detection chain main→OpenRouter→…→Anthropic, retry on 402).
**Deps:** none (cost-ledger, rubric, compaction already exist).
**Done when:** a config'd auxiliary provider is used for compaction/grading/
memory while the main provider answers; offline test asserts routing + separate
cost tagging; falls back cleanly when unset.

### E5 — Tool-arg truncation pre-pass  ·  Med · S  ·  **DONE 2026-06-09**

- [x] `context/tool_arg_truncation.py`: pure `truncate_tool_call_args` clips
      oversized string tool-call args (in `metadata["tool_calls"][i]["args"]`)
      in all but the last `protect_last` messages; head + marker, no mutation,
      returns an audit + chars_saved.
- [x] Wired as a pre-pass at the top of `apply_compaction_if_eligible` (runs
      whenever compaction is considered), guarded by
      `RunnerConfig(enable_tool_arg_truncation=…, tool_arg_truncation_max_chars=…)`;
      audit recorded under `context.metadata["tool_arg_truncation"]` (documented).
- [x] Tests: clip/protect-tail/no-op/no-mutation/non-string-skip/validation.

**Why:** cheap pre-pass avoids feeding huge old tool args to the (auxiliary)
summarizer; reduces tokens before the expensive step. Natural neighbour of E1.
**Where:** `context/compaction/` (new pre-pass step) + `context/microcompaction.py`.
**Ref:** deepagents `middleware/summarization.py` (`truncate_args_settings`),
hermes `tool_dispatch_helpers.py` (pre-pass truncation before LLM compression).
**Deps:** none; complements E1.
**Done when:** old tool-call args over the threshold are clipped pre-compaction,
audited, and reversible-safe (offload preserved); offline test pins behavior.

### E2 — Project-memory files (AGENTS.md / CLAUDE.md)  ·  High · Med  ·  **DONE 2026-06-09**

- [x] `context/project_memory.py`: `assemble_project_memory` (pure) layers
      files in source order, strips HTML comments, caps per-file + total, frames
      with reference-not-instruction guidance; `load_project_memory` does the IO.
- [x] `RunnerConfig(project_memory_sources=(...))`; injected into the system
      prompt once per run (cached in `context.metadata["project_memory_block"]`,
      documented) alongside recalled long-term memory.
- [x] Each file passes through the **E3** scanner at ingestion (poisoned files
      dropped, others survive).
- [x] Tests: assemble (order/strip/caps/skip), load+missing, scan-drop, and an
      end-to-end runner test that the block reaches the system prompt.

**Why:** standard "project memory" convention that materially improves grounding;
complements our long-term `MemoryProvider` (which is recall-based, not file-based).
**Where:** new prompt fragment in `runtime/single_agent/llm_step/prompt.py` +
a small loader (consider `agent_driver/context/project_memory.py`).
**Ref:** deepagents `middleware/memory.py` (layered AGENTS.md, override
semantics, MEMORY_SYSTEM_PROMPT), hermes `prompt_builder.py` (context files:
AGENTS.md, .cursorrules, persona).
**Deps:** **E3** should land with or before this (E3 guards ingestion).
**Done when:** project files are discovered, layered, stripped, capped, and
injected; offline test covers layering + override + cap.

### E3 — Context-file injection scanner  ·  High · S–Med  ·  **DONE 2026-06-09**

- [x] `security/context_scan.py`: `scan_context_text(text, *, source)` matches a
      curated set of prompt-injection + C2 patterns (instruction override, role
      reassignment, system-prompt probe, exfiltration, remote shell, eval
      payload) and returns `ScanResult(flagged, reasons, safe_text)` with a
      blocking placeholder; deterministic, conservative (low false-positive).
- [x] Reusable; wired into E2's `load_project_memory` (per-file, ingestion-time).
- [x] Tests: each pattern flagged, clean passes unchanged, benign "system"
      mention does not over-trigger.

Follow-up (not blocking): route the skills loader and recalled long-term memory
text through the same scanner — same one-line seam as E2.

**Why:** ingestion-time defence (not output-time) closes a real prompt-injection
hole opened by E2 and by any filesystem-sourced context.
**Where:** new `agent_driver/security/context_scan.py`; called from E2 loader,
`skills/registry.py`, memory recall rendering.
**Ref:** hermes `/tools/threat_patterns.py`, `/agent/prompt_builder.py`
(threat scan at ingestion, blocking placeholder on match).
**Deps:** pairs with E2.
**Done when:** known injection patterns in an ingested file are blocked +
flagged; offline test with a malicious fixture; clean files pass through.

### E4 — Parallel tool execution  ·  Med · Med

- [ ] Execute independent tool calls concurrently; serialize exclusive ones
      (concurrent-safe vs exclusive classes on the tool manifest).
- [ ] Preserve result ordering for the model; respect a max-concurrency cap;
      shared cancellation/abort cascades to in-flight tools.

**Why:** latency win on multi-tool turns without changing tool semantics.
**Where:** `runtime/single_agent/tool_stage/executor.py`; add a
`concurrency` hint to `ToolManifest` (`contracts/tools.py`).
**Ref:** openclaude `services/tools/StreamingToolExecutor.ts` (concurrent-safe
vs exclusive), hermes `/agent/tool_executor.py` (parallel up to N workers,
shared interrupt).
**Deps:** none.
**Done when:** independent calls run concurrently with deterministic ordering;
exclusive tools serialize; abort cancels all; offline test asserts ordering +
concurrency bound + a faster wall-clock on a fake-delayed batch.

### E6 — Per-subagent-type model routing  ·  Med · S

- [ ] Declarative `agent_type → model` routing table; subagent spawns resolve
      their model from it (with explicit-override + default fallback).

**Why:** cost optimization — cheap model for explore/verify roles, stronger for
synthesis — without code changes. We already have the pieces (subagent model
override + harness profiles); this is the declarative table on top.
**Where:** `RunnerConfig` (routing map) consumed in `subagents/` /
`sdk/subagent.py`.
**Ref:** openclaude `agentRouting` config (README "Agent Routing"); deepagents
subagent `model` override.
**Deps:** none.
**Done when:** a routing map selects per-role models at spawn; explicit spec
overrides the map; offline test covers map hit / override / default.

### E7 — Composite filesystem backend  ·  Med · L  (optional / larger)

- [ ] Pluggable backend protocol (read/write/edit/ls/glob/grep) with path-prefix
      routing: e.g. `/memories/` → persistent, `/tmp/` → ephemeral, sandbox.
- [ ] Standardized error codes (file_not_found, permission_denied, …) for
      LLM-recoverable failures.

**Why:** unifies our artifact/context stores + spill behind one tool-facing FS
abstraction; enables S3/db/sandbox backends later. Larger refactor — gate behind
explicit demand.
**Where:** new `agent_driver/fs/` backend protocol; adapt filesystem tools.
**Ref:** deepagents `backends/protocol.py`, `backends/composite.py`,
`backends/state.py`.
**Deps:** none; sizeable — schedule only if a concrete need appears.
**Done when:** path-prefix routing dispatches to the right backend with
standardized errors; offline tests per backend + composite routing.

### E8 — Message sanitization hardening  ·  Low · S

- [ ] Strip UTF-16 surrogate pairs / problematic non-ASCII before provider
      calls; harden tool-arg JSON repair; optionally strip images for
      image-phobic providers.

**Why:** defensive hygiene — several providers choke on broken UTF-8; cheap.
**Where:** `llm/` request-build path (a sanitizer pass) +
`llm/tool_call_parser.py` (extend existing repairs).
**Ref:** hermes `/agent/message_sanitization.py`.
**Deps:** none; do anytime.
**Done when:** surrogate/non-ASCII fixtures pass cleanly to a fake provider;
malformed tool-arg JSON recovers; offline tests pin both.

---

## Deferred / heavy — only with an explicit scope + dependency decision

These are valuable but carry dependency/scope weight; do not start without an
explicit go-ahead on scope:

- [ ] **N7 — platform gateway adapters + transport server**: Telegram/Slack/etc.
      delivery adapters; concrete HTTP/SSE (ASGI) and/or gRPC server binding for
      the existing headless `AgentGateway`.  Ref: hermes `/gateway/platforms/*`,
      openclaude `/src/grpc/*`.
- [ ] **ACP editor adapter** (VS Code / Zed / JetBrains).  Ref: hermes
      `/acp_adapter/*`.
- [ ] **Async / background subagents (D6)**: non-blocking spawns with task-id
      polling (start/check/update/cancel).  Ref: deepagents
      `middleware/async_subagents.py`.
- [ ] **Scope-aware HITL predicates (D6)**: fire approval only when a bulk/glob
      op could touch a protected path; `interrupt` permission mode + glob anchor.
      Ref: deepagents `middleware/filesystem.py`, `_fs_interrupt.py`.
- [ ] **Skills curator**: auto-generate + lifecycle-manage skills
      (active→stale→archived, dedupe).  Ref: hermes `/agent/curator.py`.
- [ ] **PrivateStateAttr marking**: exclude internal middleware bookkeeping from
      I/O schemas.  Ref: deepagents `PrivateStateAttr`.
- [ ] **Prompt-cache base↔memory split (D5 leftover)**: separate static base
      from per-session memory so memory churn doesn't invalidate the base cache.

---

## Sequencing

```
T0  Eval infrastructure (prerequisite; see testing-plan)      ← first
E1  Auxiliary-model routing            ─┐ cost + quality lever
E5  Tool-arg truncation pre-pass        ┘ (cheap, lives next to compaction)
E2  Project-memory files               ─┐ context + safety
E3  Context-injection scanner           ┘ (E3 guards E2)
E4  Parallel tool execution              latency
E6  Per-subagent-type model routing      orchestration polish
E7  Composite FS backend (optional, large)
E8  Message sanitization (anytime, small)
──── then only with an explicit scope decision ────
N7 / ACP / async-subagents(D6) / scope-aware HITL(D6) / skills-curator
```

Rationale: cost/quality levers first (E1/E5 build on the existing cost-ledger +
compaction), then context + safety (E2/E3), then latency (E4), then polish.
Each item ships in its own commit to `origin/main` with offline tests, per the
established cadence.

## References

Reference repos (cloned locally under `/home/user/_refs/`):

- hermes-agent — `/home/user/_refs/hermes-agent`
- openclaude — `/home/user/_refs/openclaude`
- deepagents — `/home/user/_refs/deepagents`

Prior analysis: [review-cycle-2](review-cycle-2-2026-06-09.md),
[gap-analysis-and-plan](gap-analysis-and-plan-2026-06-09.md),
[extending](extending.md).
