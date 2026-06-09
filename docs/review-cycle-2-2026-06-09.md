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
| **N1** | **Cost governance** — budget ceiling with fail-fast when a run/session exceeds it, a cost summary, opt-in structured per-request token logging, per-provider cache hit-rate | both refs; builds on `cost_ledger` + `UsageSummary` (already carries cache tokens) | High · Low |
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

## Recommendation

Sequence: **N2** (cheap, closes a built-but-unwired loop) → **N1** (high-value
cost governance) → **N6** (pay robustness before growing further) → **N3 / N4 /
N5** → **N7** only with an explicit scope + dependency decision.
