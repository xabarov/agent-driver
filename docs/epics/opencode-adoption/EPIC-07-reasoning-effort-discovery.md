# EPIC-07 — Reasoning-effort capability discovery + reject-before-I/O (M)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Also the
[deepseek-harness survey](../../../) candidate — landed once here. Source idea: opencode's
`transform.ts` validates the requested reasoning effort against the model before the call.

## The foot-gun

`contracts/reasoning.py` already documents it: the universal graded tiers
`low`/`medium`/`high` are accepted by every reasoning backend, but the finer tiers
(`minimal`/`xhigh`/`max`) are honored natively only by some backends (Anthropic, the newest
OpenAI models) and are **clamped or rejected mid-stream** by other OpenRouter routes. A run
that sets `reasoning_effort="xhigh"` against an unsupporting route discovered the failure
only from a broken streaming response — after the request was already in flight.

## What was built

A small curated capability table + a synchronous pre-flight in the provider adapter, so an
unsupported tier fails **before any network I/O** with a clear error.

- `agent_driver/llm/reasoning_effort_support.py`:
  - `_FAMILY_SUPPORTED_EFFORTS` — family substrings matched against the lowercased model id
    (first match wins, mirroring `context_windows.py`): Anthropic/`claude` accept all tiers
    (native thinking maps effort→budget and *clamps*, never rejects); OpenAI reasoning
    models (`gpt-5`/`o1`/`o3`/`o4`) accept `minimal`+`low`/`medium`/`high` (reject
    `xhigh`/`max`, which aren't OpenAI enum values); non-reasoning OpenAI chat (`gpt-4`/
    `gpt-3.5`) accept only the universal set. An **unknown** model returns `None` →
    permissive.
  - `validate_effort_for_model(effort, model)` — raises `UnsupportedReasoningEffortError`
    (a `ValueError`, so it composes with the existing effort-validation errors) **only** for
    a *fine* tier that a *known* model does not support. Universal tiers and unknown models
    always pass — so there are **zero false rejects** on the common path; we reject only
    when the table is confident the call would fail. This is the precise scope that fixes
    the documented foot-gun.
  - `effort_from_reasoning_envelope(envelope)` — recovers the tier from the provider-neutral
    reasoning envelope (`{"effort": t}` → `t`; `{"enabled": False}` → `"none"`).
- `providers_impl/openai_compatible/__init__.py`: a `_preflight_reasoning(request)` call at
  the top of both `complete()` and `stream()`, **before** `execute_with_telemetry` / the
  stream setup — so the error propagates straight to the caller and is never swallowed by
  the retry wrapper or turned into a provider-status retry. The Anthropic provider is not
  gated (it clamps rather than rejects, and `claude` is all-supported anyway).

Tests (`tests/llm/test_reasoning_effort_support.py`): the table (universal always ok,
Anthropic all-fine, non-reasoning OpenAI rejects fine, o-series allows `minimal` rejects
`max`, unknown permissive, envelope extraction) + the provider pre-flight (an unsupported
fine tier raises before I/O on `complete`/`stream`; a portable tier is a no-op). Full
`tests/llm` sweep + the cross-suite reasoning/effort/thinking tests stay green.

## Deliberately conservative

- **Reject only known-unsupported fine tiers.** No attempt to reject reasoning on a
  non-reasoning model for the *universal* tiers (they're harmlessly ignored) — that would
  risk false rejects for no safety gain. The table encodes capability facts, not
  benchmark-tuning (per repo `CLAUDE.md`).
- **No live models.dev fetch.** A hand-curated substring table (like `context_windows.py`),
  not a network capability probe — deterministic and dependency-free. A models.dev-backed
  refresh can extend the table later without changing the validation contract.
