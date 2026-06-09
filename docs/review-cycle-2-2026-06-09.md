# Review Cycle 2 — Next Gaps (2026-06-09)

Second best-practices pass after closing the original gap plan (#1–#10) + two
architecture rounds. Re-scanned NousResearch/hermes-agent and Gitlawb/openclaude
for ideas *beyond* what now exists, plus an adversarial self-audit of the new
packages. Claims below were verified against the current code.

## Verified facts that shaped this list

- `observability/cost_ledger.py` tracks per-(model, session) tokens + USD but
  has **no budget enforcement / summary** — only pricing validation.
- The `CONTEXT_OVERFLOW` reason produced by `llm/error_classifier.py` (#3) is
  **not consumed anywhere** — the compress-and-retry loop it was designed for
  was never wired.
- Hook chains **already** have per-rule `cooldown_seconds` + `depth_limit`
  (`contracts/hook_chains.py`); only a **dedup window** and **outcome/field
  filtering** are missing.
- Compaction **already** has a circuit breaker (`context/compaction/
  orchestrator.py` `failure_limit` + `CIRCUIT_BREAKER_OPEN`) — so "naive retry"
  is not a real gap.

## Next-cycle candidates (ranked)

| # | Item | Why / builds on | Value · Risk |
|---|------|-----------------|--------------|
| **N1** ✅ | **Cost governance** (DONE 2026-06-09) — `CostRuntimeState` accumulates per-LLM-call usage into a `CostLedger`; `AgentRunInput.cost_budget_usd` is enforced fail-fast in `_terminal_from_limits` (`TerminalReason.BUDGET_EXCEEDED`); added `CostLedger.cache_hit_rate` + `format_cost_summary`. Tests: ledger accumulation, cache-hit/summary, budget over/under/none, validator. Deferred: opt-in structured per-request token logging | both refs; built on `cost_ledger` + `UsageSummary` | High · Low |
| **N2** ✅ | **Reactive compaction on `CONTEXT_OVERFLOW`** (DONE 2026-06-09) — `complete_request` gained a single-shot `recover_context_overflow` callback; `execute_llm_call_step` forces a compaction at `blocking` pressure, rebuilds a smaller request, and retries once. Circuit breaker bounds storms. Tests: unit recovery/single-shot/no-callback + end-to-end runner recovery | closed the loop #3 left open | High · Low |
| **N3** | **Goal tracking** — `goal` lifecycle (active/achieved) + evaluator injected into the prompt; distinct from planning | openclaude `/goal`; lands on the A1 lifecycle-hook seam | Med-High · Low |
| **N4** | **Hook-chain enrichment** — add a dedup window + trigger on outcome/event-field filters | openclaude hookChains; incremental on existing executor | Med · Low |
| **N5** | **Trajectory compression for training** — compress batch trajectories within a token budget, preserving first/last turns | hermes `trajectory_compressor`; extends batch #9 | Med · Low |
| **N6** | **Robustness/quality pass** (self-audit) — per-hook error isolation in lifecycle dispatch; `close()`/consolidation for the 7 remaining bespoke sqlite stores; wire `MemoryProvider.post_setup/shutdown`; CLI flags for memory/permission; a runnable scheduler/gateway daemon example; document batch concurrency + permission-mode thresholds | self-review of the new packages | Med · Low |
| **N7** | **Heavy/opt-in (needs scoping + deps)** — real platform adapters (Telegram/Slack) + pairing + delivery routing (hermes gateway); an HTTP/SSE server for the gateway core (deferred #6); environment backends (Docker/SSH/Modal) | hermes gateway/environments; deferred #6 | High · High (deps) |

## Self-audit notes (accurate subset)

- **Lifecycle hook dispatch has no per-hook isolation** (`runtime/lifecycle_hooks.py`)
  — one failing `on_run_start` blocks the rest; `HookChainLifecycleHook` already
  isolates per-spawn, so mirror that. (N6)
- **7 sqlite stores still hand-roll plumbing** and lack `close()`
  (`runtime/sqlite_store.py`, `runtime/control/sqlite.py`,
  `context/{artifacts,sessions,planning}`, `subagents/{store,mailbox}`); B1's
  `SqliteStoreBase` is the target to consolidate onto. (N6)
- **Memory provider `post_setup`/`shutdown` are never called** by the runtime. (N6)
- **Memory + permissions are only reachable from the SDK**, not the CLI. (N6)
- Non-issues this pass: the compaction circuit breaker already exists;
  `Registry.values()` `id()` dedup is correct for the current use (aliases point
  to the same object).

## Addendum — third reference: LangChain `deepagents` (2026-06-09)

Added `deepagents` (langchain-ai/deepagents) as a third lens. Its harness is a
**middleware composition** over a LangChain agent (TodoList / Filesystem+perms /
SubAgent(+async) / Summarization / HumanInTheLoop / AnthropicPromptCaching, plus
Memory / Rubric / Skills / PatchToolCalls) selected by **profiles**
(HarnessProfile/ProviderProfile). Compared against agent-driver's current state;
verified each claim against the code.

Already covered (not gaps): eager oversized-tool-output offloading with a
preview + fetch-by-reference is already `tools/executor/spill.py`
(+ `context_window_recovery.py`); curated skills, durable subagents, the
permission layer, compaction ladder + reactive overflow recovery are all
present.

Genuinely new/better directions from deepagents:

| # | Item | Why / builds on | Value · Risk |
|---|------|-----------------|--------------|
| **D2** ✅ | **Per-LLM-call hook seam** (DONE 2026-06-09) — `RunLifecycleHook` gained `before_llm_request` (chained, returns a replacement request) + `after_llm_response`; dispatched around the provider call in the LLM step. Tests: both fire end-to-end; a before-hook's transform reaches the provider | generalized the A1 seam to the LLM-call boundary; enables D3 + middleware-style extensions | High · Med |
| **D3** ✅ | **Rubric / grader goal-gate** (DONE 2026-06-09) — `on_finalize` now returns `RevisionRequest \| None`; the runner's `_execute_finalize` builds a bounded revision continuation (injects grader feedback as a user turn, loops to `llm_call`) capped by `_MAX_RUBRIC_REVISIONS=10`. `RubricLifecycleHook(criteria, grade_fn, *, max_iterations=3)` delegates the "done?" decision to a host-supplied `GradeFn → GraderVerdict(satisfied, feedback)`; `RubricRuntimeState` records per-iteration verdicts (`rubric_iterations`/`rubric_evaluations`). Tests: revise-once-then-accept, feedback reaches the model, `max_iterations` bound. Grading stays host-owned (subagent/structured-call/test-runner) — runtime does not prescribe how "done" is judged | lands on the `on_finalize` hook (D2 seam) — **supersedes N3** | High · Med |
| **D4** | **Harness profile layer** — declarative per-provider/model prompt-assembly slots (USER→BASE/CUSTOM→SUFFIX) + per-model excluded tools/middleware + tool-description overrides, validated at assembly | a declarative layer over descriptor providers (#8) + prompt templates | Med · Med |
| **D5** | **Anthropic prompt-cache breakpoints** — place `cache_control` at prompt-assembly boundaries (static base → memory → conversation) so updates don't invalidate the cached prefix | ties to **N1** cache hit-rate observability; real Anthropic cost win | Med · Low |
| **D6** | Refinements: scope-aware HITL predicates (fire approval only when a bulk/glob op could touch a protected path) for the #7 permission gate; `PrivateStateAttr`-style marking of internal state; async/background subagents with task-id polling | incremental on #7 / state owners / subagents | Low-Med · varies |

Net: deepagents' middleware model is orthogonal, not strictly superior to the
step-loop + lifecycle hooks — but **D2** (per-call seam) and **D3** (rubric
goal-gate) are sharper than what we have and worth adopting; **D3 replaces the
earlier N3**.

## Recommendation

N2 is done. Updated sequence: **N1** (high-value cost governance, + **D5**
prompt-cache breakpoints fold in here) → **D2** (per-LLM-call hook seam — the
enabling architecture that **D3** and future middleware-style extensions ride
on) → **D3** (rubric goal-gate, replaces N3) → **N6** (robustness pass) → **N4 /
N5 / D4** → **N7 / D6 / async-subagents** only with an explicit scope +
dependency decision.
