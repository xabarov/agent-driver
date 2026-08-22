# EPIC-09 — Progressive tool-catalog disclosure + `search` (M)

Status: **DONE (2026-08-22)**. Track: [opencode-adoption](README.md). Source idea:
opencode's `codemode` discovery inlines only part of a big tool/MCP catalog and lets the
model discover the rest — ~80% of code-as-action's prompt-economy benefit with **zero
interpreter/sandbox risk** (see the survey's REJECTED note on a full code-as-action plane).

## What we already had

- **`search`** — `tools/builtin/tool_search.py` already registers a `tool_search` tool that
  queries registered manifests by name/description + risk/side-effect filters. The
  discover-the-tail half was present.
- **Deferral** — `adaptive_defer_surface` (`llm_step/build.py`) already applied the hermes
  `should_activate` threshold to `should_defer` candidates: **under** the window fraction
  it force-surfaces them all (cheaper inline than a `tool_search` round-trip); **at/above**
  it deferred — but all-or-nothing, surfacing **nothing**.

## The gap: all-or-nothing → namespace-fair partial disclosure

When a big MCP catalog crosses the threshold, surfacing *nothing* forces the model to
`tool_search` blind, and there was no fairness — a single huge server could dominate any
future selection. EPIC-09 adds opencode's progressive disclosure:

- `_tool_namespace(name)` — buckets a tool by namespace (`mcp__server__tool` →
  `mcp__server`; plain builtins → `""`).
- `_round_robin_disclosure(candidates, per_tool_tokens, *, budget_tokens)` — groups by
  namespace and fills **round-robin by rank**, so every namespace is offered a tool before
  any gets a second, stopping at the token budget. Pure + unit-tested.
- `adaptive_defer_surface(..., disclosure_budget_tokens)` — when deferral activates AND
  the budget is `> 0`, it inlines the round-robin slice (a fair teaser of every namespace)
  instead of nothing; the tail defers and stays discoverable via `tool_search`. Audit gains
  `disclosure_mode`/`surfaced_count`/`deferred_count`/`disclosure_tokens_used`.
- Config `CapabilitySettings.tool_defer_disclosure_budget_tokens` (**default 0** →
  historical all-or-nothing; behaviour-neutral), threaded through the request-build ctx.

Tests (`tests/runtime/test_progressive_disclosure.py`): namespace bucketing, round-robin
fairness + budget bound, zero-budget no-op, and end-to-end through `adaptive_defer_surface`
(budget 0 surfaces nothing; a budget inlines exactly a fair one-per-namespace slice and
defers the tail). Full `tests/runtime` + `tests/tools` + export snapshot green (the
CapabilitySettings field snapshot was updated).

## Deliberately scoped

- **Opt-in via a positive budget.** Default 0 keeps the well-tested all-or-nothing path, so
  no existing deployment changes behaviour; a host with a big MCP catalog sets a budget to
  get fair partial inlining. Complete + tested, one knob to enable.
- **`batch_tool_call` NOT built.** The optional declarative batch-of-independent-calls (the
  second half of the candidate) is deferred — the `run_subagent_group` join vocab already
  covers dependent/parallel fan-out, and a batch primitive risks re-treading the
  [[ssb-tool-adoption-lesson]] (a new tool that clones an adopted one gets zero calls). The
  disclosure win stands on its own.
- **Reused the existing `tool_search`** rather than adding a second search surface.
