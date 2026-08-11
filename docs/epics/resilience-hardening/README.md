# Resilience hardening (F-track)

Harness fault-tolerance epics, seeded by a 2026-08-10 survey of `agent-driver`
against the reference runtimes in `reference/{hermes-agent, openclaude, openhands,
openhands-sdk}`.

## Where we stand

agent-driver is **top-of-pack** on harness resilience — STRONG on 8 of 10
dimensions, and ahead of every reference on timeouts (3 fuses: request + liveness
idle + run deadline), streaming recovery (fallback-to-nonstreaming + force-final
from partial), cancellation correctness (`AbortRequested`, honest
`CANCELLATION_FAILED`), and `Retry-After` honoring. Error taxonomy is a 15-class
deterministic classifier (`llm/error_classifier.py`); retry lives at three layers;
graceful degradation (budget-grace, 402→reduced max_tokens) and stuck/loop guards
(`tool_stage/guards.py`) are STRONG.

The real gaps are narrow and concentrated. This track closes them. Each epic names
the reference that proves the pattern and keeps the runtime **domain-neutral** — no
benchmark-fitting, harness-layer only.

## Epics

| ID | Epic | Gap | Proven by | Effort | Status |
| --- | --- | --- | --- | --- | --- |
| **F1** | Decorrelated backoff jitter | fixed 2^n → lockstep retries / thundering herd | hermes, openclaude | S | **DONE** |
| **F2** | Per-provider circuit breaker | health has no cooldown/half-open/threshold/persistence | hermes | M | **DONE** |
| **F3** | Honor more server directives | only `Retry-After`; ignore `x-should-retry` + rate-limit-reset | openclaude, hermes | M | **DONE** |
| **F4** | Ordered fallback-model list | only provider-swap + forced-final fallback, no model-tier list | all 3 refs | M | **DONE** |
| **F5** | Small correctness wins | abort-blind backoff sleeps; no nudge-before-kill | openclaude, openhands | S | **DONE** |
| **F6** | Shared retry budget | 3 retry layers can compound (base 4× × completion 3×) | — (internal) | M | **DONE** |

Deprioritized: **cost-cascade / draft-then-verify** (our documented future,
`llm/model_router.py:19-23`) — no reference implements a true cost-cascade, so it is
not a competitive gap.

---

### F1 — Decorrelated backoff jitter — DONE (2026-08-10)

`llm/backoff.jittered_delay` adds additive-only jitter (`delay + rand·0.25·delay`,
never below `delay` so a server `Retry-After` stays honored) at all five backoff
sites (`llm/base.py` status + stream-open, `llm_step/completion.py` transient-status
+ transport blind-retry, `batch/runner.py`). RNG is a patchable module seam.

### F2 — Per-provider circuit breaker — DONE (2026-08-11)

`HealthAwareRouter` (`llm/router.py`) now keeps a sticky per-provider circuit-breaker
state machine (`_BreakerState`): `circuit_failure_threshold` (default 3) consecutive
unhealthy-marking failures **open** the circuit; `_ranked_candidates` excludes an open
provider for `circuit_cooldown_seconds` (default 30s) **regardless of `status.healthy`**
— closing the flap where `refresh_health()` re-marked a completions-failing provider
healthy every call; after the cooldown the circuit goes **half-open** and the next
attempt is a single probe (success closes, failure re-opens with an exponentially
escalated cooldown capped at `circuit_cooldown_max_seconds`, default 300s).
Request-level failures (auth / content-policy) never trip it. `circuit_breaker_enabled`
opt-out; `now` clock seam for tests. Tests: `tests/llm/test_router_circuit_breaker.py`.

Follow-on (F2b, PROPOSED): the **stale-streak give-up that fires before the network
call** (hermes `_check_stale_giveup`, `chat_completion_helpers.py:334`; per-provider
cooldown `nous_rate_guard.py`) — count consecutive unresponsive streams across turns
and jump straight to fallback without another network wait; complements the breaker for
the "wedged on a dead provider, looping for hours" mode.

### F3 — Honor more server directives — DONE (2026-08-11)

New `llm/retry_directives.py`: `parse_should_retry` (obey `x-should-retry` — `false`
fails fast, wired into `llm/base.py`'s status loop + the completion loop's transient
retry) and `rate_limit_reset_seconds` (parse any `*ratelimit*reset*` header —
relative-seconds / epoch / ISO-8601 — and fold it into the backoff via
`strongest_retry_delay`, waiting the longer of base / `Retry-After` / reset, capped so
a large reset can't wedge a bounded loop). Injectable epoch clock. Composes with F1
jitter. Tests: `tests/llm/test_retry_directives.py` + the completion integration in
`tests/runtime/test_llm_step_retry.py`. (Refs: openclaude `withRetry.ts:854,1065`;
hermes `adaptive_rate_limit_backoff`.) Deferred (F3b): provider-aware adaptive-backoff
tiers and duration-string reset formats (`"6m0s"`) — provider-adapter territory.

### F4 — Ordered fallback-model list — DONE (2026-08-11)

`complete_request` now wraps the per-error retry loop (`_complete_request_attempts`)
in an ordered model-fallback loop: after the primary model's in-place retries are
exhausted, a non-fatal failure retries the whole attempt on each id in
`RunnerConfig(fallback_models=(…,))` (threaded to `RunnerDeps` alongside
`fallback_providers`), rewriting `request.model`, until one succeeds. Gated by the
same `is_fatal` rule as provider-fallback (auth / content-policy / context-overflow
don't fall back); cost/events accumulate on the shared host; a `model_fallback`
warning names the failed and next model. Distinct from F2 (proactive *provider*
circuit-breaker) — this is a reactive *model* swap. Tests:
`tests/runtime/test_model_fallback.py`. (Refs: OpenHands `FallbackStrategy`, hermes
`get_fallback_chain`, openclaude `FallbackTriggeredError`.)

### F5 — Small correctness wins — DONE (2026-08-11)

(a) **Abort-responsive backoff sleeps** — `llm/backoff.abort_aware_sleep` polls a
cooperative abort in `poll_seconds` slices; the completion loop's transient-status
and transport backoffs use it (abort_check derived from `context.abort_handle`), so a
Stop during a backoff is honored within a slice instead of after the full wait.
(b) **Nudge-before-kill on the tool-failure streak** — `_update_tool_failure_guard`
sets `tool_failure_nudge_due` at the streak-warning point (one below the force-final
threshold), and `recovery._append_tool_failure_streak_nudge` appends a one-time
model-facing self-correction message before the next identical failure hard-forces
the final. Tests: `tests/llm/test_backoff_jitter.py` (abort-sleep),
`tests/runtime/test_tool_failure_nudge.py`. (Orphaned-tool-call backfill was NOT
needed — `context_management/protocol_validate.py` already inserts
`missing_tool_result_stubs` + folds orphans.)

### F6 — Shared retry budget across layers — DONE (2026-08-11)

`RunnerConfig(completion_retry_budget_seconds=…)` (threaded to `RunnerDeps` alongside
`fallback_models`) is a single wall-clock budget the completion retry loop
(`_complete_request_attempts`) checks at the top of each attempt: once cumulative
time passes it, the loop raises the last error instead of re-entering the provider —
bounding the base(~4)×completion(~3) multiplication end-to-end. `None` (default)
keeps the plain 3-attempt behavior; `_monotonic` is a patchable clock seam. Tests:
`tests/runtime/test_completion_retry_budget.py`.

## Status

**F1–F6 all DONE (2026-08-11).** agent-driver's harness resilience — already
top-of-pack in the reference survey — is now hardened across every gap the survey
found: backoff jitter, provider circuit breaker, server directives, model fallback,
abort-responsive backoff + nudge-before-kill, and a shared retry budget. Remaining
optional follow-on: **F2b** (stale-streak give-up-before-network-call, hermes
`_check_stale_giveup`).
