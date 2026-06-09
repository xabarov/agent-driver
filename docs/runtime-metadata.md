# Runtime Metadata Inventory

Status: reference / current inventory for
[Unified Work Plan](unified-work-plan-2026-05-31.md#phase-1---runtime-state-and-contract-foundation).

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
| `resume_action`, `resume_message`, `resume_target_step`, `pending_interrupt`, `interrupt_payload` | resume/interrupt flow, output builder | checkpoint/output | yes for resume/interrupt UI | `LoopControlState` plus interrupt contract |
| `tool_results`, `tool_trace`, `tool_calls`, `tool_loop_iterations`, `max_tool_calls` | tool stage, output, research contract | output/checkpoint | yes, trace/debug | `ToolLoopState` |
| `skill_invocations`, `invoked_skill_refs` | `skill_view` post-processing, output/compaction projection | output/checkpoint | yes for Skills UI and trace/debug | `ToolLoopState` plus `CompactionRuntimeState` projection |
| `unknown_tool_counts`, `denied_tool_counts`, `last_denied_signature`, `approved_tool_call` | tool governance and repair loops | checkpoint mostly | diagnostics | `ToolLoopState` |
| `effective_tool_names`, `tool_choice_override`, `force_final_answer`, `force_final_answer_reason`, `forced_tool_choice_retry`, `forced_tool_catalog` | llm/tool-call preparation and repair | checkpoint | diagnostics | `ToolLoopState` or `ProviderRuntimeState` |
| `planning_state`, `planning_step`, `planning_state_seed` | step planning, output, research contract | output/checkpoint | yes | `PlanningRuntimeState` |
| `approved_plan`, `clarification`, `last_todo_write_signature`, `todo_write_deduped` | planning tools, approval flow, output | output/checkpoint | yes for planning UI | `PlanningRuntimeState` |
| `last_in_progress_id`, `todo_hint_count_step1`, `todo_reminder_tool_loops`, `tool_loops_since_todo_write` | todo nudges and reminders | checkpoint | diagnostics | `PlanningRuntimeState` |
| `research_session_contract`, `final_readiness`, `repair_required_reasons` | research contract/final readiness | output/checkpoint | yes for trace/debug | `ResearchRuntimeState` |
| `contract_repair_nudge_count`, `contract_repair_reason_signature`, `continuation_nudge_count`, `continuation_nudge_reason` | research/todo repair loops | checkpoint | diagnostics | `ResearchRuntimeState` |
| `web_search_calls_total`, `web_search_zero_streak`, `web_fetch_calls_total` | research tool accounting | output/checkpoint | trace/debug | `ResearchRuntimeState` |
| `web_fetch_verification_hint_sent`, `web_fetch_verification_hint_sent_for`, `web_fetch_duplicate_guard_sent` | research discipline nudges | checkpoint | diagnostics | `ResearchRuntimeState` |
| `research_fetch_fallback_required`, `research_avoid_domains`, `research_source_diversity_avoid_domains` | research repair/source diversity | checkpoint | diagnostics | `ResearchRuntimeState` |
| `deep_research_parent_review_required` | deep-research parent verify+review repair forcing | checkpoint | diagnostics | `ResearchRuntimeState` |
| `assistant_stream_started`, `assistant_stream_content`, `assistant_stream_completed` | streaming LLM step/output recovery | checkpoint | yes for stream UI | `StreamingRuntimeState` |
| `assistant_stream_tombstoned`, `assistant_stream_recovered`, `assistant_stream_recovery_reason` | stream recovery | output/checkpoint | diagnostics | `StreamingRuntimeState` |
| `raw_assistant_content`, `last_llm_response`, `llm_call_started_monotonic` | LLM step/output builder | checkpoint/runtime | diagnostics | `StreamingRuntimeState` or `ProviderRuntimeState` |
| `trim_audit`, `trim_metadata`, `token_pressure`, `previous_token_pressure_state`, `prompt_render` | deterministic trimming / prompt render / pressure state-change diagnostics | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `microcompaction`, `microcompaction_audit`, `post_compact_cleanup` | context compaction/microcompaction | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `active_compaction_id`, `compaction_decision`, `compaction_audit`, `compaction_result`, `compaction_failures` | compaction stage/orchestrator | output/checkpoint | yes for compaction UI | `CompactionRuntimeState` |
| `session_memory_extraction`, `retained_artifact_ids`, `retained_digest_ids` | output/memory compaction | output/checkpoint | trace/debug | `CompactionRuntimeState` |
| `planned_subagent_group`, `subagent_groups`, `subagent_runs`, `subagent_merge_summary`, `subagent_origin` | subagent stage/output | output/checkpoint | yes for subagent UI | `SubagentRuntimeState` |
| `artifact_refs`, `digest_refs`, `observations`, `protocol_messages`, `parse_error_feedback_sent_keys` | output builder, context stores, protocol validation | output/checkpoint | yes for diagnostics | `OutputRuntimeState` |
| `prompt_fragments`, `code_tool_docs`, `python_policy_hint_sent` | prompt/profile policy | checkpoint | diagnostics | `OutputRuntimeState` or prompt-render state |
| `last_provider_error`, `max_tokens_retry`, `empty_forced_final_retry`, `forced_final_retry`, `reasoning_echo_retry` | provider retry/recovery | checkpoint | provider diagnostics | `ProviderRuntimeState` |
| `applied_controls`, `workspace_cwd`, `eval_sandbox_dir` | control dispatcher / runner env | output/checkpoint | diagnostics | `LoopControlState` or run input metadata |
| `recalled_memory`, `memory_synced` | long-term memory prefetch (run start) / one-time sync guard (finalize) | checkpoint | diagnostics | memory provider hooks (`MemoryProvider`) |

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
