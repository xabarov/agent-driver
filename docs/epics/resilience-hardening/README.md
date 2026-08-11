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
| **F3** | Honor more server directives | only `Retry-After`; ignore `x-should-retry` + rate-limit-reset | openclaude, hermes | M | PROPOSED |
| **F4** | Ordered fallback-model list | only provider-swap + forced-final fallback, no model-tier list | all 3 refs | M | **DONE** |
| **F5** | Small correctness wins | abort-blind backoff sleeps; no nudge-before-kill | openclaude, openhands | S | PROPOSED |
| **F6** | Shared retry budget | 3 retry layers can compound (base 4× × completion 3×) | — (internal) | M | PROPOSED |

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

### F3 — Honor more server directives

We honor `Retry-After` (ahead of OpenHands). Add: obey an `x-should-retry` response
header (openclaude `withRetry.ts:854`), and in a long-wait/persistent mode **sleep
until** the `*-ratelimit-*-reset` header instead of fixed backoff (openclaude
`:1065`); optionally provider-aware adaptive-backoff tiers (hermes
`adaptive_rate_limit_backoff`). Composes with F1's jitter.

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

### F5 — Small correctness wins

Two cheap, independent fixes: (a) **abort-responsive backoff sleeps** — our
`asyncio.sleep` in the retry paths is not abort-checked, so a cooperative abort set
during a backoff wait isn't noticed until it elapses (openclaude `utils/sleep.ts`
throwOnAbort). (b) **Nudge-before-kill on the tool-failure streak** — we have nudge
machinery (`tool_stage/research.py`, plan-verification) but the same-signature
failure streak goes straight to force-final; inject a one-time self-correction
message ("this call keeps failing, change approach") before hard-stopping (OpenHands
`stuck_detector.py:218`). (Note: orphaned-tool-call backfill is NOT needed — we
already insert `missing_tool_result_stubs` + fold orphans in
`context_management/protocol_validate.py`.)

### F6 — Shared retry budget across layers

Base-layer status retries (up to 4) × completion-layer (3) × batch-layer can
multiply into long compounding stalls on a persistently failing provider, each layer
unaware of the others. Thread a shared attempt/time budget so total retry work is
bounded end-to-end. Internal cleanup; lower user-visible impact.

## Recommended order

F1 (done) → F2 (done) → F4 (done) → **F3** → F5 → F6 · plus F2b (stale-streak
give-up).
