# Unified Work Plan

Status: slim living record. Originally created 2026-05-31; trimmed 2026-06-23
once the bulk of the plan shipped.

Purpose: keep one short truth for the work that is actually left. The detailed
phase checklists this page used to carry are now closed — see the
[June 2026 archive](archive/2026-06/README.md) and
[May 2026 archive](archive/2026-05/README.md) for the decision history.

## Shipped (do not reopen from stale checkboxes)

The earlier unified phases 0–8 plus the 2026-05/06 follow-on cycles are done:

- artifact-first Deep Research: session workspace, scoped file-write tools,
  `research/report.md`, `file_patch`, stale-/repeated-read guards, write-through
  guard for oversized assistant messages, todo/patch repair instead of full
  rewrite (Phases 1–2);
- research storage + artifact-aware compaction: durable `sources.jsonl` ledger,
  oversized tool-output spill with stable refs, artifact/source summaries
  projected after compaction (Phase 3);
- eval harness: deterministic artifact / rewrite-loop / source-ledger /
  provider-failure scenarios + reusable eval result contract across CLI,
  pytest and the chat-demo backend (Phase 4, deterministic part);
- storage backend convergence: one checkpoint serialization source of truth
  shared across memory/sqlite/jsonl/postgres, shared ordering + capability
  tests (Phase 5);
- SDK gateway / tool server: the OpenAI-compatible HTTP/SSE server, async runs,
  Responses API, MCP-HTTP and A2A adapters shipped under optional extras
  (Phase 6 — see the platform-adapters plan in the June archive).

## Remaining work (narrow)

1. Deep Research hard-profile hardening. Real page-aware PDF extraction ships
   behind the optional `[pdf]` extra (`pdf_read`, 2026-06-23). Hard claim
   auditing (`research/claims.jsonl`) is now enforceable at final-readiness via
   `hard_options.enforce_claims_audit` (opt-in, 2026-06-23). Remaining: enable
   it by default only behind a green chat-demo health check; the phase gate
   stays soft/optional by design.

2. Live cost discipline for the eval harness. Deterministic scenarios pass; the
   live GPT-5.5 cost-regression gate is operational, not code. Keep the live
   ladder cheap-to-expensive; record run IDs only when they explain a current
   regression or acceptance result.

3. Deferred-by-choice. N7 heavy platform adapters (Telegram/Slack + delivery
   routing) and the remaining ACP client methods (`tool_terminal_ref`,
   `session/set_model`, `elicitation/*`) wait on explicit demand plus a
   scope/dependency decision.

## Active design inputs

- [Efficient Deep Research Workspace Architecture](efficient-deep-research-workspace-architecture-2026-05-31.md)
- [Provider And Model Debugging](provider-model-debugging.md)
- [Runtime Metadata Inventory](runtime-metadata.md)
- [Research Quality Summary](research-quality-improvement-plan-2026-05-31.md)

## Ongoing docs rule

- Keep [docs/README.md](README.md) and [docs/roadmap.md](roadmap.md) short.
- Closed plans go to `archive/` or become compact decision records.
- Reference docs must not carry active-looking unchecked checklists unless the
  items are also present here.
- Provider run IDs stay only where they explain current acceptance or a live
  regression class.
