# Runtime Metadata Inventory

Status: reference / current inventory for
[Unified Work Plan](archive/unified-work-plan-2026-05-31.md#phase-1---runtime-state-and-contract-foundation).

Date: 2026-05-31.

Purpose: make current `RunContext.metadata` usage explicit before replacing
ad hoc keys with typed runtime state helpers. This page is an ownership map,
not a public SDK contract.

## Ground Rules

- New runtime state should go through owned helpers or typed state objects, not
  new unowned `context.metadata[...]` keys.
- `AgentRunOutput.metadata` must stay compatibility-preserving while internal
  state is migrated.
- Public SDK contracts should not freeze undocumented metadata names.
- Runtime-only keys may remain internal even if they are copied into output
  metadata today; the migration should decide which fields are public,
  diagnostic or private.

## Proposed Typed State Owners

| Owner | Purpose | Candidate helper/state |
| --- | --- | --- |
| Loop control | step routing, max steps, terminal output, resume targets | `LoopControlState` |
| Tool loop | tool results, traces, denials, unknown tools, tool-call counters, skill invocation records | `ToolLoopState` |
| Planning | live todo state, approval plan payloads, dedupe hints | `PlanningRuntimeState` |
| Research | fetch/search counters, final readiness, repair nudges, source diversity | `ResearchRuntimeState` |
| Streaming | assistant streaming lifecycle and recovery flags | `StreamingRuntimeState` |
| Compaction/context | trimming, token pressure, micro/LLM compaction, memory extraction | `CompactionRuntimeState` |
| Subagents | planned groups, child runs, merge summaries, origin metadata | `SubagentRuntimeState` |
| Artifacts/output | artifact refs, digest refs, retained IDs, observations | `OutputRuntimeState` |
| Provider retry | provider errors and compatibility retries | `ProviderRuntimeState` |

## Current Key Map

| Key(s) | Producer / consumer | Persistence | UI relevance | Migration target |
| --- | --- | --- | --- | --- |
| `next_step`, `step_count`, `llm_step_count`, `max_steps`, `terminal_output` | single-agent loop, runner output | checkpoint/output | terminal diagnostics | `LoopControlState` |
| `resume_action`, `resume_message`, `resume_target_step`, `pending_interrupt`, `interrupt_payload`, `consumed_approvals` | resume/interrupt flow, output builder | checkpoint/output | yes for resume/interrupt UI | `LoopControlState` plus interrupt contract; `consumed_approvals` records consumed interrupt/idempotency ids for U3 idempotent-replay detection |
| `tool_results`, `tool_trace`, `tool_calls`, `tool_loop_iterations`, `max_tool_calls`, `empty_answer_retry_count`, `degenerate_answer_retry_count`, `empty_tool_calls_reprompt_count` | tool stage, output, research contract | output/checkpoint | yes, trace/debug | `ToolLoopState` (`empty_answer_retry_count`: epic 015 bounded empty-answer re-prompt; `degenerate_answer_retry_count`: epic 015 bounded canned/wrong-language refusal re-prompt; `empty_tool_calls_reprompt_count`: epic 042 B bounded re-prompt on finish_reason=tool_calls with empty array) |
| `skill_invocations`, `invoked_skill_refs` | `skill_view` post-processing, output/compaction projection | output/checkpoint | yes for Skills UI and trace/debug | `ToolLoopState` plus `CompactionRuntimeState` projection |
| `unknown_tool_counts`, `denied_tool_counts`, `last_denied_signature`, `approved_tool_call`, `disallowed_management_tool_hint_sent` | tool governance and repair loops | checkpoint mostly | diagnostics | `ToolLoopState` |
| `effective_tool_names`, `tool_choice_override`, `force_final_answer`, `force_final_answer_reason`, `forced_tool_choice_retry`, `forced_tool_catalog` | llm/tool-call preparation and repair | checkpoint | diagnostics | `ToolLoopState` or `ProviderRuntimeState` |
| `budget_grace_granted_at_step`, `budget_grace_reason` | soft-budget grace (`_terminal_from_limits`): records that one forced-final synthesis window was opened on step/tool-call exhaustion and the original budget reason | checkpoint | diagnostics | `LoopControlState` |
| `planning_state`, `planning_step`, `planning_state_seed` | step planning, output, research contract | output/checkpoint | yes | `PlanningRuntimeState` |
| `approved_plan`, `clarification`, `last_todo_write_signature`, `todo_write_deduped` | planning tools, approval flow, output | output/checkpoint | yes for planning UI | `PlanningRuntimeState` |
| `last_in_progress_id`, `todo_hint_count_step1`, `todo_reminder_tool_loops`, `tool_loops_since_todo_write` | todo nudges and reminders | checkpoint | diagnostics | `PlanningRuntimeState` |
| `research_session_contract`, `final_readiness`, `repair_required_reasons` | research contract/final readiness | output/checkpoint | yes for trace/debug | `ResearchRuntimeState` |
| `contract_repair_nudge_count`, `contract_repair_reason_signature`, `continuation_nudge_count`, `continuation_nudge_reason` | research/todo repair loops | checkpoint | diagnostics | `ResearchRuntimeState` |
| `web_search_calls_total`, `web_search_zero_streak`, `web_fetch_calls_total` | research tool accounting | output/checkpoint | trace/debug | `ResearchRuntimeState` |
| `web_fetch_verification_hint_sent`, `web_fetch_verification_hint_sent_for`, `web_fetch_duplicate_guard_sent` | research discipline nudges | checkpoint | diagnostics | `ResearchRuntimeState` |
| `research_fetch_fallback_required`, `research_avoid_domains`, `research_source_diversity_avoid_domains` | research repair/source diversity | checkpoint | diagnostics | `ResearchRuntimeState` |
| `deep_research_parent_review_required` | deep-research parent verify+review repair forcing | checkpoint | diagnostics | `ResearchRuntimeState` |
| `deep_research_active_profile`, `deep_research_context_active`, `deep_research_artifacts`, `deep_research_child_synthesis`, `deep_research_strategy_tool_choice`, `deep_research_inline_answer_max_chars` | deep-research profile/context/artifact + strategy bookkeeping | checkpoint | diagnostics | `ResearchRuntimeState` (research gating) |
| `deep_research_initial_subagent_gate`, `deep_research_initial_subagent_recovery`, `deep_research_initial_subagent_batch_clamped`, `deep_research_initial_direct_discovery_coerced`, `deep_research_parent_synthesis_gate`, `deep_research_parent_synthesis_required`, `deep_research_parent_synthesis_recovery`, `deep_research_parent_synthesis_tool_coerced`, `deep_research_parent_artifact_batch_clamped`, `deep_research_artifact_repair_gate`, `deep_research_artifact_repair_batch_coerced`, `deep_research_file_write_args_repaired`, `deep_research_terminal_handoff_gate`, `deep_research_terminal_tool_calls_suppressed` | deep-research tool gating / repair / handoff payloads (see `runtime/single_agent/research/gating.py`) | checkpoint | diagnostics | research gating policy |
| `forced_tool_choice_disabled`, `llm_request_allowed_tools`, `subagent_backpressure` | forced tool-choice toggle, per-request allowed-tool snapshot, subagent backpressure flag | checkpoint | diagnostics | `ToolLoopState` / `SubagentRuntimeState` |
| `assistant_stream_started`, `assistant_stream_content`, `assistant_stream_completed`, `assistant_stream_events_seen`, `assistant_stream_token_chunks_seen`, `assistant_stream_reasoning_chunks_seen`, `assistant_stream_tool_intent_seen` | streaming LLM step/output recovery and stream event counters | checkpoint | yes for stream UI | `StreamingRuntimeState` |
| `assistant_stream_tombstoned`, `assistant_stream_recovered`, `assistant_stream_recovery_reason` | stream recovery | output/checkpoint | diagnostics | `StreamingRuntimeState` |
| `raw_assistant_content`, `last_llm_response`, `llm_call_started_monotonic` | LLM step/output builder | checkpoint/runtime | diagnostics | `StreamingRuntimeState` or `ProviderRuntimeState` |
| `trim_audit`, `trim_metadata`, `token_pressure`, `previous_token_pressure_state`, `prompt_render` | deterministic trimming / prompt render / pressure state-change diagnostics | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `microcompaction`, `microcompaction_audit`, `post_compact_cleanup` | context compaction/microcompaction | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `planning_state_reinjected`, `artifact_refs_reinjected`, `rubric_reinjected`, `recalled_memory_reinjected` | steering state re-injected after a compaction rewrites the prompt (epic 035 D single-point invariant: plans/artifacts/goal-gate rubric/recalled-memory survive compaction) | output/checkpoint | trace/debug | `apply_post_compact_cleanup` |
| `tool_arg_truncation` | E5 pre-pass audit: chars saved + per-arg clips when oversized tool-call args in older messages are truncated before compaction | checkpoint | trace/debug | `CompactionRuntimeState` |
| `tool_history_compression` | epic-035-A pre-pass audit: chars saved + truncated/stubbed counts when OLD tool-result bulk is tiered-compressed for stateless providers | checkpoint | trace/debug | compaction stage (`compress_tool_history`) |
| `active_compaction_id`, `compaction_decision`, `compaction_audit`, `compaction_result`, `compaction_failures` | compaction stage/orchestrator | output/checkpoint | yes for compaction UI | `CompactionRuntimeState` |
| `session_memory_extraction`, `retained_artifact_ids`, `retained_digest_ids` | output/memory compaction | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `planned_subagent_group`, `subagent_groups`, `subagent_runs`, `subagent_merge_summary`, `subagent_origin` | subagent stage/output | output/checkpoint | yes for subagent UI | `SubagentRuntimeState` |
| `parent_run_id`, `subagent_group_id` | subagent child-run handoff identity (set by the subagent executor, read by the runner for the EPIC-03 default ISOLATE lease policy — a child run neither acquires nor releases the parent's execution lease) | checkpoint | diagnostics | `SubagentRuntimeState` |
| `artifact_refs`, `digest_refs`, `observations`, `protocol_messages`, `parse_error_feedback_sent_keys` | output builder, context stores, protocol validation | output/checkpoint | yes for diagnostics | `OutputRuntimeState` |
| `prompt_fragments`, `code_tool_docs`, `python_policy_hint_sent` | prompt/profile policy | checkpoint | diagnostics | `OutputRuntimeState` or prompt-render state |
| `last_provider_error`, `last_provider_diagnostics`, `last_provider_stream_error`, `max_tokens_retry`, `provider_max_tokens_source`, `empty_forced_final_retry`, `forced_final_retry`, `forced_final_empty_after_all_retries`, `forced_final_prior_turn_recovered`, `forced_final_fallback_provider`, `poisoned_prefix_quarantine_attempted`, `poisoned_prefix_suspect_turns`, `poisoned_prefix_quarantine_recovered`, `wall_clock_guard`, `tool_failure_guard`, `refunded_tool_calls`, `context_window_resolved`, `context_window_unresolved_warned`, `reasoning_echo_retry`, `transient_provider_retries`, `transient_transport_retries`, `prompt_cache_state`, `context_overflow_recovery`, `provider_stream_non_stream_fallback`, `provider_stream_fallback_diagnostics` | provider retry/recovery (incl. reactive compact-and-retry on context overflow and streaming fallback diagnostics) | checkpoint | provider diagnostics | `ProviderRuntimeState` |
| `applied_controls`, `workspace_cwd`, `eval_sandbox_dir` | control dispatcher / runner env | output/checkpoint | diagnostics | `LoopControlState` or run input metadata |
| `execution_lease_ref`, `execution_lease_failure`, `execution_lease_receipts` | EPIC-03 execution-lease manager (runner) — the SAFE, non-secret, generation-bound `ExecutionLeaseRef` (JSON) re-attached on resume; a redaction-safe fail-closed reason when a requested lease could not be secured; and per-phase lease timing receipts (queue/acquire/ready/release/detach, `duration_ms`) for independent observation | checkpoint | diagnostics | `agent_driver.execution` lease manager |
| `tool_output_budget` | raw-free per-run rollup of the epic-033 tier-3 per-turn output-budget pass (`spilled_count`, `chars_saved`) — how much tool-output tax was trimmed | checkpoint | diagnostics | tool stage (`enforce_turn_output_budget`) |
| `redirect_count_step` | hard-redirect anti-storm counter per LLM step (эпик 030 B) | transient | diagnostics | run metadata |
| `llm_generation`, `live_message_terminal_reconciliation` | live-message generation fence and raw-free terminal promotion/Stop reconciliation | checkpoint/output | live-control diagnostics | versioned live-message control state |
| `recalled_memory`, `memory_synced` | long-term memory prefetch (run start) / one-time sync guard (finalize) | checkpoint | diagnostics | memory provider hooks (`MemoryProvider`) |
| `memory_recall_count` | raw-free count of long-term memory records recalled at run start (epic 021 observability) | checkpoint | diagnostics | memory provider hooks (`MemoryProvider`) |
| `memory_consolidation` | raw-free outcome of a background consolidation pass (`applied`, `reason`, `before`/`after` counts) when the cadence gate lands (epic 031) | checkpoint | diagnostics / governance notice | memory provider hooks (`MemoryProvider.consolidate`) |
| `project_memory_block` | E2 layered project-memory (AGENTS.md/CLAUDE.md) block, loaded + E3-scanned once per run and injected into the system prompt | checkpoint | diagnostics | project-memory loader (prompt build) |
| `cost_ledger` | per-run token/USD cost ledger accumulated per LLM call; drives `cost_budget_usd` fail-fast | checkpoint | cost diagnostics | `CostRuntimeState` |
| `context_breakdown`, `effective_context_budget` | 0.3.3 context-integrity: public per-run context breakdown (message/metadata/tool-schema char + token accounting, agreeing with the compaction trigger) and the resolved run-scoped budget (`RunContextBudget` / `app_metadata.context_budget`); raw-free | output/checkpoint | context/budget diagnostics | `CompactionRuntimeState` (`resolve_run_context_budget`) |
| `rubric_revision_count`, `rubric_iterations`, `rubric_evaluations` | goal-gate (rubric) revision loop counter + per-iteration grader verdicts | checkpoint | diagnostics | `RubricRuntimeState` + finalize revision continuation |

## Related Non-Context Metadata

These are not `RunContext.metadata` keys but still affect the public/runtime
state boundary:

- `AgentRunInput.app_metadata`: caller hints such as stream polling interval,
  approved prompts, forced model and sandbox/workspace hints.
- `ToolPolicy.metadata`: chat policy and task contract inputs, including
  `task_contract`, `planning_hint`, `force_planning`, `deliverable_request`,
  `research_request` and `plan_only_request`.
- `LlmResponse.metadata` / streaming event metadata: provider-normalized
  payload such as `planned_tool_calls`, `tool_call_parse_errors`,
  `provider_profile`, `reasoning_details`, token chunks and text-form tool
  call flags.
- `ToolManifest.metadata` and tool result metadata: tool catalog capabilities,
  security policy, queue/category hints, Python executor facts and source
  metadata.
- Subagent task/group/run metadata: worker role/type, handoff policy, join
  state, continuation messages and child artifact audits.

## Migration Order

1. Done for Phase 1: add small typed wrappers around the highest-churn groups:
   planning, research, compaction/context and tool loop. `get_*_state(context)`
   helpers now provide the preferred entry point for new runtime code.
2. Done for Phase 1: replace direct writes in `runtime/single_agent/*` with helper
   calls while preserving the same serialized metadata.
   Completed first slice: `RunContext` loop/tool counters, terminal-output
   lookup, workspace-cwd lookup, planning event emission and forced-final /
   tool-choice controls in `tool_stage.py`.
   Completed second slice: terminal/paused output compaction projection,
   interrupt payload, approved-plan lookup, raw assistant content and stream
   recovery bookkeeping.
   Completed final Phase 1 slice: research contract consumers, tool-result
   consumers, todo reminder counters, planning updates, LLM trim/microcompaction
   payloads, tool-choice reads and source-verified repair paths.
   Remaining direct metadata writes are producer-owned stage internals
   (`compaction_stage.py`, `resume.py`, subagent bookkeeping) and should move
   during the structural refactor/SDK-diagnostics phases if they become public
   surface.
3. Done: add tests that assert helpers preserve current `AgentRunOutput.metadata`
   shape.
4. Decide which keys graduate to documented SDK diagnostics and which remain
   internal trace fields.
5. Done: `tests/runtime/test_runtime_metadata_inventory.py` requires new
   literal runtime `context.metadata` keys to be added to this inventory in the
   same change.
