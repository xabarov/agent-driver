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
| 06 | M | **Real outward MCP client** ✅ | **DONE → [`EPIC-06`](EPIC-06-mcp-client.md).** `agent_driver/tools/mcp_client/`: `StdioMcpClient` (dependency-free JSON-RPC over subprocess stdio) + `HttpMcpClient` (streamable-HTTP over `httpx`, SSE + `Mcp-Session-Id`) — both **live-verified** against `@modelcontextprotocol/server-everything`; transport-agnostic `register_mcp_client` + `register_stdio/http_mcp_server` (governed, namespaced `mcp__<server>__<tool>`); **OAuth2+PKCE** helpers (`oauth.py`); SDK wiring `sdk.connect_mcp_servers`/`close_mcp_servers`; **ACP `mcp_servers`** wiring (`adapters/acp/mcp.py`, deduped into the shared agent); **`tools/list_changed`** refresh (`resync_mcp_server_tools` + `ToolRegistry.unregister`). Deferred: interactive OAuth loopback/dynamic-registration + HTTP list_changed (need the GET SSE stream). |
| 07 | M | **Reasoning-effort capability discovery + reject-before-I/O** ✅ | New `llm/reasoning_effort_support.py`: a curated per-model effort table (substring families, like `context_windows.py`) + `validate_effort_for_model`, wired as `_preflight_reasoning` at the top of the OpenAI-compatible provider's `complete`/`stream` — an unsupported *fine* tier (`minimal`/`xhigh`/`max`) on a known-unsupporting model raises `UnsupportedReasoningEffortError` **before any network I/O** instead of a mid-stream OpenRouter rejection. Universal tiers + unknown models pass (zero false rejects). **DONE → [`EPIC-07`](EPIC-07-reasoning-effort-discovery.md).** Also the deepseek-track candidate — landed once. |
| 08 | S/M | **Promote ToolResultPruner to a live default tier** ✅ | New `_apply_live_tool_result_prune` pre-pass in `apply_compaction_if_eligible`, wired **independently of `enable_compaction`** (next to the tool-arg/tool-history pre-passes): under token pressure it clears OLD tool-result content (keeping recent `keep_recent`) in the **ephemeral request** (log intact), committing only above a char threshold. `RunnerConfig.live_tool_prune_enabled` default **True** (neutral until pressure). Fires on the deterministic-trim path excel-ai actually runs. **DONE → [`EPIC-08`](EPIC-08-live-tool-pruner.md).** |
| 09 | M | **Progressive tool-catalog disclosure + `search`** ✅ | The `tool_search` tool + threshold-based deferral already existed; the gap was all-or-nothing disclosure. Added `_round_robin_disclosure` + `disclosure_budget_tokens` to `adaptive_defer_surface`: when deferral activates and a budget is set, inline a **token-budgeted, round-robin-across-namespace** slice (fair teaser of every namespace) instead of nothing; the tail defers to `tool_search`. `CapabilitySettings.tool_defer_disclosure_budget_tokens` default 0 (behaviour-neutral). **DONE → [`EPIC-09`](EPIC-09-progressive-disclosure.md).** `batch_tool_call` deferred (run_subagent_group covers fan-out; avoids the clone-tool-gets-zero-calls trap). |
| 10 | M | **Provider-overflow (413) emergency compaction** ✅ | The reactive-overflow path (`is_context_window_error` + `_overflow_recovery` force-compact + rebuild) already existed; the gap was that a single retry may not free enough when `enable_compaction=False` and the bulk is a large/media payload. Added `emergency_strip_oversized_payloads` — wholesale-clears OLD tool results (keep newest 1) + hard-caps any oversized message (embedded blob/media) — wired into `_recover` on the rebuilt request, `RunnerConfig.overflow_emergency_strip_enabled` default True (only fires on an actual overflow). Typed `context_overflow_emergency_strip` audit. **DONE → [`EPIC-10`](EPIC-10-overflow-emergency-strip.md).** |
| 11 | L | **Durable, resumable subagent identity** 🟡 | **Stage 1 (addressable durable identity) DONE → [`EPIC-11`](EPIC-11-durable-subagent-identity.md).** Stack-B already persisted every child as a `SubagentRun` with a `child_run_id`, but only addressable by parent. Added `SubagentStore.find_run_by_child_run_id` (InMemory scan + Sqlite JSON1, survives restart) + `Agent.find_subagent_run(child_run_id)` — a child is now addressable by its own id. **Deferred (the risky rest of the L):** Stage 2 `resume_subagent(child_run_id, prompt)` (needs a stable caller-addressable child_run_id + checkpoint-resume entry point), a PG-backed `SubagentStore`, and fg→bg `promote` / running-child `extend`. |

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
