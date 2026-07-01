# June 2026 Documentation Archive

Status: archive.

Closed plans land here once their work is shipped, so the current docs stay
short while the decision history remains available. Each line notes what shipped;
verify against code/`CHANGELOG.md` rather than re-opening a stale checkbox.

## Cross-harness backlog (delivered)

- [Gap analysis and horizontal work plan](gap-analysis-and-plan-2026-06-09.md) —
  A1–A5 architecture tracks + error taxonomy, memory, scheduler, hook-chains,
  permissions, descriptor providers, MCP server, gateway, batch. All shipped.
- [Improvement plan E1–E8](improvement-plan-e1-e8-2026-06-09.md) — auxiliary
  routing, project memory, context scanner, tool concurrency, tool-arg
  truncation, subagent routing, fs backends, message sanitization, eval
  infrastructure (T0). All shipped; the four unchecked items are intentional
  deferrals (N7 heavy adapters, prompt-cache base↔memory split).
- [Review cycle 2](review-cycle-2-2026-06-09.md) — N1–N6 + D2–D5 shipped (cost
  governance, reactive compaction, hook-chain enrichment, trajectory
  compression, robustness, per-LLM-call hook seam, rubric goal-gate, harness
  profiles, prompt-cache breakpoints). N3 was superseded by D3. Only N7 (heavy
  platform adapters) is deferred.
- [SDK refactor + review cycle 3](sdk-refactor-and-review-cycle-3-2026-06-09.md) —
  R1–R5 (capability config, construction-time tool_gate, self-audit fixes, docs
  + cookbook 10–15, scoped CLI eval axes). Shipped.
- [Testing & comparison plan](testing-plan-2026-06-09.md) — T0 eval harness
  (N-run aggregation, baseline-vs-treatment, open-weight presets, general task
  suite, `eval compare` CLI). Shipped.

## Platform adapters & protocols (delivered)

- [Platform adapters plan](platform-adapters-plan-2026-06-10.md) — Phase 1 ACP,
  Phase 2 OpenAI-compatible HTTP/SSE (+ async runs, Responses API), Phase 3
  MCP Streamable-HTTP, Phase 4 A2A. All shipped.
- [ACP deepening plan](acp-deepening-plan-2026-06-10.md) — client-side fs +
  terminal callbacks, rich `session/update` (plan, mode, commands, edit diffs),
  session list/fork/close. Tiers 1–3 shipped; `tool_terminal_ref`,
  `session/set_model` and `elicitation/*` deferred pending demand.
- [Node contract plan](node-contract-plan-2026-06-11.md) — Layers A (policy↔
  registry validation), B (tool-use contract + reprompt), C (early finalize
  from tool evidence). Shipped; consumer guide is `docs/node-contract.md`.
- [Phoenix / OpenInference tracing plan](phoenix-openinference-tracing-plan-2026-06-04.md) —
  agent/LLM/tool spans with semantic conventions + token/cost attrs. Shipped.

## Refactors & design (delivered)

- [Refactoring plan](refactoring-plan-2026-06-10.md) — Tier A (shim hygiene,
  network-surface de-dup), Tier B + C god-module splits
  (`run_trace/summary.py`, `cli/evals.py`). Shipped; Tier C3 (research modules)
  deferred by design.
- [Python sandbox design](python-sandbox-design-2026-06-04.md) — isolated
  `run_sandboxed` primitive (RLIMIT/network/builtins/import controls). Shipped
  in `agent_driver/code_agent/sandbox.py`.
