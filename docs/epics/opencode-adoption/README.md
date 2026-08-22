# opencode-adoption

Adoption track distilled from the comparative survey of `reference/opencode`
(anomalyco/opencode — 200k★ Effect-TS coding agent) against our runtime. Full
findings + rationale: memory `opencode-survey`. Only the transferable, domain-neutral
ideas are here; product/SaaS/coding-agent-specific features (hosted account/share,
git-snapshot workspace undo, a real LSP fleet, a full code-as-action plane) are
**rejected** for our library boundary (see the survey's "REJECTED" section).

Sequenced cheapest-first (S → M → L). Each epic is behaviour-neutral or opt-in unless
noted; land in small test-gated increments per repo `CLAUDE.md`.

## Epics

| # | Size | Epic | One-line |
|---|------|------|----------|
| 01 | S | **Import-layering guard** ✅ | AST-test that `contracts`/`sdk`/`embedding` never import `runtime.single_agent.*` internals — mechanically kills the excel-ai reach-in class. **DETAILED → [`EPIC-01`](EPIC-01-import-layering-guard.md).** |
| 02 | S | **Doom-loop / repetition detector** ✅ | Ring of last-N `(tool_name, canonical_args_hash)`; 3 identical consecutive → force the existing budget-grace synthesis turn (or a HITL redirect), not a silent step cap. **DONE → [`EPIC-02`](EPIC-02-doom-loop-detector.md)** (the detector already existed hardcoded at 2; made the threshold configurable, default 2). |
| 03 | S | **Decision-hook seam on the gate chain** ✅ | Ordered host-registered `ToolDecisionHook`s that run *after* `evaluate_tool_policy` + the dynamic `ToolGate` and may only *tighten* (allow→interrupt→deny, never loosen past a hard DENY); a raising hook fails **closed** to DENY; optional steering `feedback` is folded into the model-facing reason. From opencode `permission.ask`. Lets excel-ai/Zion inject governance without forking. **DONE → [`EPIC-03`](EPIC-03-decision-hook-seam.md).** Schema-rewrite (opencode `tool.definition`) deliberately deferred — a separate catalog-assembly concern. |
| 04 | S | **Correcting-rejection feedback + pending-cascade** ✅ | An approval rejection carrying operator `feedback` denies the call but *continues* the run, folding the feedback into the next model turn as steering (opencode `CorrectedError`); a bare reject still aborts (`RejectedError`). Opt-in via `RunnerConfig.corrective_rejection_enabled` (default off). **DONE → [`EPIC-04`](EPIC-04-correcting-rejection.md).** The sibling **pending-cascade** is N/A in our single-agent loop (exactly one interrupt is ever pending; approve-forward already covered by `approved_prompts`) — deferred to any future concurrent-ask layer. |
| 05 | S | **Structured summary template + rolling-update contract** ✅ | Our `build_full_compaction_prompt` already emitted a structured JSON summary + B2 rolling; the two real gaps are now closed: a required `completed_work` bucket (opencode's "Completed" work-state) and the carry-forward-or-lose rolling contract (prior summary discarded → carry standing context forward; newer slice wins on conflict; move finished work to Completed) + a verbatim-preservation rule for exact paths/commands/errors/URLs. **DONE → [`EPIC-05`](EPIC-05-structured-summary-template.md).** Kept JSON shape (not opencode markdown); `session_memory` deterministic extraction left as-is. |
| 06 | M | **Real outward MCP client** 🟡 | **stdio slice DONE → [`EPIC-06`](EPIC-06-mcp-client.md).** New `agent_driver/tools/mcp_client/`: a real, **dependency-free** `StdioMcpClient` (JSON-RPC 2.0 over subprocess stdio — handshake, paginated `tools/list`, `tools/call`, resources, timeouts, error mapping) + `register_stdio_mcp_server` that discovers tools and registers them governed + namespaced (`mcp__<server>__<tool>`, EXTERNAL_ACTION/ON_POLICY_MATCH, allowlist). Tested end-to-end against a fake stdio server. **Deferred (the rest of the M):** HTTP/streamable-HTTP/SSE transports (need an HTTP client / the Python `mcp` SDK — likely an optional `agent-driver[mcp]` extra), OAuth2+PKCE, config-driven server list + ACP `mcp_servers` wiring, `tools/list_changed` refresh. The `tools/builtin/mcp.py` fixtures stub stays for back-compat. |
| 07 | M | **Reasoning-effort capability discovery + reject-before-I/O** ✅ | New `llm/reasoning_effort_support.py`: a curated per-model effort table (substring families, like `context_windows.py`) + `validate_effort_for_model`, wired as `_preflight_reasoning` at the top of the OpenAI-compatible provider's `complete`/`stream` — an unsupported *fine* tier (`minimal`/`xhigh`/`max`) on a known-unsupporting model raises `UnsupportedReasoningEffortError` **before any network I/O** instead of a mid-stream OpenRouter rejection. Universal tiers + unknown models pass (zero false rejects). **DONE → [`EPIC-07`](EPIC-07-reasoning-effort-discovery.md).** Also the deepseek-track candidate — landed once. |
| 08 | S/M | **Promote ToolResultPruner to a live default tier** | Run the already-built `ToolResultPruner` (in `condenser_tiers.py`) independently of the dormant condenser pipeline — on token-pressure, default-on, protect a recent-N-token window + protected tools, clear older tool results in the ephemeral view (log stays intact), commit only if it frees a threshold. Fires even when `enable_compaction=False`, so it targets the path excel-ai *actually* runs. From opencode `prune` (Anthropic `clear_tool_uses`). |
| 09 | M | **Progressive tool-catalog disclosure + `search` (+ optional `batch_tool_call`)** | For large tool/MCP sets: inline only a token-budgeted, round-robin-across-namespace slice of the catalog + route the tail through the existing `ToolSearch` deferred-tools. Optionally add a declarative `batch_tool_call` (list of independent calls + join, reusing `run_subagent_group` join semantics). Captures ~80% of codemode's benefit (prompt economy + no round-trip on independent calls) with **zero interpreter/sandbox risk**. From opencode `codemode` discovery. |
| 10 | M | **Provider-overflow (413) emergency compaction** | On a provider `context_overflow`/413-class error (detected in the resilience/retry path), invoke a last-resort compaction: strip large/media tool payloads from the view, force a summary of the head, replay the last user turn, continue; emit a typed diagnostic. Covers the case our *estimate*-based trigger misses. From opencode `overflow.ts` / `processCompaction`. |
| 11 | L | **Durable, resumable subagent identity** | `sdk.resume_subagent(child_run_id, prompt)` — back `run_subagent` with a journaled child run keyed by a `child_run_id` persisted in the PG control-plane (Stage 1, M), then resume/continue it (Stage 2, L). Unifies our two fragmented subagent stacks (SDK in-proc/ephemeral vs runtime persisted stack-B) onto substrate we already own. opencode's single biggest structural advantage (subagent = durable session). Also enables fg→bg `promote` + running-child `extend` (from opencode `background/job.ts`) as follow-ons. |

## Not in this track (rejected — see survey)

Full Python code-as-action plane (security ownership vs library boundary — if ever
built, an opt-in *remote-sandboxed* execution-backend provider, keeping
`nooa-code-as-action-candidate` AST-validation as the reference); Effect-TS
service/layer composition (our `Protocol`-injection is the Python analog); hosted
account/org/session-share (product/SaaS → belongs in excel-ai); git-snapshot workspace
undo (coding-agent-specific → consumer-side); a real stdio LSP fleet (borrow only the
verify-after-edit *pattern*, generically).

## Convergence with the deepseek track

EPIC-07 (reasoning-effort capability discovery) is the SAME candidate the
[deepseek-harness survey](../../../) flagged — land it once. The deepseek track's
credential-reference seam pairs naturally with EPIC-06 (MCP auth) and provider auth.
