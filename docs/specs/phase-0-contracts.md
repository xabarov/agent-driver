# Phase 0 Contracts Spec

Date: 2026-05-18

Status: draft for implementation

## Purpose

Phase 0 defines the minimal stable contracts for `agent-driver` before writing the runtime implementation. These contracts are the shared language between graph execution, checkpointing, streaming, tool governance, interrupts, observability, evaluation, and future API adapters.

The first code milestone should implement these contracts as typed Python models and schema tests. Runtime behavior can stay fake/minimal until the contracts are importable and stable.

## Design Rules

- Prefer Pydantic models for public serialized contracts.
- Keep internal implementation free to use dataclasses where serialization is not required.
- All public timestamps use UTC ISO-8601 strings.
- Every event has `event_id`, `run_id`, `attempt_id`, and `seq`.
- Every resumable event should include `checkpoint_id` when available.
- Keep application identifiers opaque: `user_id`, `tenant_id`, `workspace_id`, and `app_metadata` are not interpreted by the engine.
- Model outputs, tool outputs, and traces must support redaction/sensitivity metadata.
- Unknown future fields should be accepted in `metadata` dictionaries, not as arbitrary top-level sprawl.

## Identifier Types

Use strings for IDs in public contracts:

- `run_id`: one top-level agent run.
- `attempt_id`: one execution attempt for a run.
- `thread_id`: durable conversation/thread.
- `checkpoint_id`: persisted graph state snapshot.
- `branch_id`: alternate timeline forked from a checkpoint.
- `event_id`: unique runtime event.
- `tool_call_id`: model/tool call correlation id.
- `subagent_run_id`: child agent run id.
- `interrupt_id`: pending human review/approval id.
- `artifact_id`: stored context/artifact pointer.
- `trace_id`: observability trace id.

The implementation may provide helpers like `new_run_id()`, but the contract should not require UUID specifically.

## Enum Contracts

### RunStatus

- `queued`
- `running`
- `paused`
- `completed`
- `failed`
- `cancelled`
- `timed_out`

### TerminalReason

- `final_answer`
- `cancelled_by_user`
- `deadline_exceeded`
- `max_steps_exceeded`
- `tool_policy_denied`
- `guardrail_blocked`
- `approval_rejected`
- `runtime_error`
- `model_error`
- `checkpoint_error`

### RuntimeEventType

- `run_started`
- `run_queued`
- `run_resumed`
- `node_started`
- `node_completed`
- `llm_call_started`
- `llm_call_completed`
- `token_delta`
- `tool_call_started`
- `tool_call_completed`
- `guardrail_decision`
- `checkpoint_saved`
- `interrupt_requested`
- `run_paused`
- `subagent_started`
- `subagent_completed`
- `artifact_created`
- `memory_compacted`
- `warning`
- `run_completed`
- `run_failed`
- `run_cancelled`

### ToolRisk

- `low`
- `medium`
- `high`

### SideEffectClass

- `none`
- `read_only`
- `reversible_write`
- `irreversible_write`
- `external_action`

### ApprovalMode

- `never`
- `on_policy_match`
- `always`
- `step_by_step`

### InterruptReason

- `approval_required`
- `clarification_required`
- `guardrail_review`
- `tool_args_review`
- `state_review`
- `manual_pause`

### ResumeAction

- `approve`
- `reject`
- `edit`
- `clarify`
- `patch_state`
- `cancel`

### SubagentTerminalState

- `succeeded`
- `failed`
- `cancelled`
- `killed`
- `timed_out`

## Core Models

### AgentRunInput

Purpose: app-facing request to start or continue an agent run.

Fields:

- `input`: `str | None`
- `messages`: `list[ChatMessage]`
- `thread_id`: `str | None`
- `run_id`: `str | None`
- `resume`: `ResumeCommand | None`
- `agent_id`: `str`
- `graph_preset`: `str`
- `model_role`: `str`
- `tool_policy`: `ToolPolicyInput`
- `deadline_seconds`: `float | None`
- `max_steps`: `int | None`
- `max_tool_calls`: `int | None`
- `user_id`: `str | None`
- `tenant_id`: `str | None`
- `workspace_id`: `str | None`
- `app_metadata`: `dict[str, Any]`

Validation:

- Either `input`, `messages`, or `resume` must be present.
- `deadline_seconds`, if set, must be positive.
- `max_steps` and `max_tool_calls`, if set, must be positive.
- `graph_preset` defaults should be resolved by config, not hard-coded in the contract.

### ChatMessage

Purpose: minimal engine-neutral chat message.

Fields:

- `role`: `system | user | assistant | tool`
- `content`: `str`
- `name`: `str | None`
- `tool_call_id`: `str | None`
- `metadata`: `dict[str, Any]`

Notes:

- Provider-specific message shapes belong in `llm/` adapters.
- Multimodal content can be added later through typed content blocks.

### AgentRunOutput

Purpose: normalized result for sync and completed streaming runs.

Fields:

- `run_id`: `str`
- `attempt_id`: `str`
- `thread_id`: `str | None`
- `status`: `RunStatus`
- `answer`: `str | None`
- `messages`: `list[ChatMessage]`
- `events`: `list[RuntimeEvent]`
- `tool_trace`: `list[ToolTrace]`
- `subagent_runs`: `list[SubagentRun]`
- `artifacts`: `list[ArtifactRef]`
- `usage`: `UsageSummary | None`
- `warnings`: `list[RunWarning]`
- `trace`: `TraceRef | None`
- `checkpoint`: `CheckpointRef | None`
- `interrupt`: `InterruptRequest | None`
- `memory_audit`: `dict[str, Any] | None`
- `terminal_reason`: `TerminalReason | None`
- `metadata`: `dict[str, Any]`

Validation:

- `status == paused` requires `interrupt`.
- terminal statuses should include `terminal_reason`.
- `run_completed`, `run_failed`, or `run_cancelled` event should appear for terminal output.

### RuntimeEvent

Purpose: canonical event emitted by the runtime and adapted to SSE/WebSocket/JSONL.

Fields:

- `event_id`: `str`
- `type`: `RuntimeEventType`
- `run_id`: `str`
- `attempt_id`: `str`
- `seq`: `int`
- `created_at`: `str`
- `checkpoint_id`: `str | None`
- `node_id`: `str | None`
- `payload`: `dict[str, Any]`
- `trace_id`: `str | None`
- `severity`: `debug | info | warning | error`
- `redaction`: `RedactionInfo | None`

Validation:

- `seq` starts at 1 and increases within an attempt.
- `payload` must be JSON-serializable.
- token events should keep token text inside `payload["delta"]`.

### CheckpointRef

Purpose: stable pointer to resumable graph state.

Fields:

- `checkpoint_id`: `str`
- `run_id`: `str`
- `attempt_id`: `str`
- `thread_id`: `str | None`
- `branch_id`: `str | None`
- `parent_checkpoint_id`: `str | None`
- `graph_id`: `str`
- `node_id`: `str | None`
- `created_at`: `str`
- `state_version`: `str`
- `storage_backend`: `str`
- `metadata`: `dict[str, Any]`

Notes:

- This is a reference, not the checkpoint payload.
- The checkpoint payload may include LangGraph state, channel versions, and serializer metadata.

### ResumeCommand

Purpose: command to continue a paused run.

Fields:

- `interrupt_id`: `str`
- `action`: `ResumeAction`
- `message`: `str | None`
- `edited_tool_args`: `dict[str, Any] | None`
- `state_patch`: `dict[str, Any] | None`
- `approved_by`: `str | None`
- `created_at`: `str | None`
- `metadata`: `dict[str, Any]`

Validation:

- `edit` requires `edited_tool_args` or `state_patch`.
- `clarify` requires `message`.
- `approve` and `reject` should not include arbitrary state mutation.

### InterruptRequest

Purpose: persisted human review or approval request.

Fields:

- `interrupt_id`: `str`
- `run_id`: `str`
- `attempt_id`: `str`
- `checkpoint_id`: `str`
- `reason`: `InterruptReason`
- `title`: `str`
- `description`: `str`
- `risk`: `ToolRisk | None`
- `proposed_action`: `dict[str, Any]`
- `allowed_actions`: `list[ResumeAction]`
- `editable_fields`: `list[str]`
- `expires_at`: `str | None`
- `metadata`: `dict[str, Any]`

Notes:

- For tool approvals, `proposed_action` should include `tool_name`, `tool_call_id`, and argument preview.
- UI adapters should not need to parse arbitrary traces to render approval cards.

### ToolTrace

Purpose: normalized audit row for one tool call.

Fields:

- `step`: `int`
- `tool_name`: `str`
- `tool_call_id`: `str | None`
- `status`: `started | completed | failed | denied | timed_out`
- `args_summary`: `dict[str, str]`
- `result_summary`: `str | None`
- `artifact_refs`: `list[ArtifactRef]`
- `risk`: `ToolRisk`
- `side_effect`: `SideEffectClass`
- `approval_mode`: `ApprovalMode`
- `duration_ms`: `int | None`
- `error_code`: `str | None`
- `truncated`: `bool`
- `metadata`: `dict[str, Any]`

Validation:

- High-risk side-effecting tools should record policy decision metadata.
- Large results should use `artifact_refs` plus bounded summaries.

### ToolPolicyInput

Purpose: per-run policy knobs supplied by app or defaults.

Fields:

- `mode`: `allow_tools | no_tools | clarify | approval_required`
- `allowed_tools`: `list[str] | None`
- `denied_tools`: `list[str] | None`
- `max_tool_calls`: `int | None`
- `approval_required_for_risk`: `ToolRisk | None`
- `metadata`: `dict[str, Any]`

### SubagentRun

Purpose: canonical row for child agent or specialist execution.

Fields:

- `subagent_run_id`: `str`
- `parent_run_id`: `str`
- `parent_attempt_id`: `str`
- `parent_checkpoint_id`: `str | None`
- `child_run_id`: `str | None`
- `task_id`: `str`
- `task_type`: `str`
- `description`: `str`
- `execution_mode`: `sync | background`
- `fanout_slot`: `int`
- `status`: `pending | running | completed | failed | cancelled | timed_out`
- `terminal_state`: `SubagentTerminalState | None`
- `latency_ms`: `int | None`
- `tokens`: `UsageSummary | None`
- `cost_usd_estimate`: `float | None`
- `failure_code`: `str | None`
- `output_pointer`: `ArtifactRef | None`
- `merge_provenance`: `MergeProvenance | None`
- `metadata`: `dict[str, Any]`

Validation:

- terminal rows require `terminal_state`.
- completed rows should include either `output_pointer`, merge metadata, or both.

### MergeProvenance

Purpose: describe how child output was merged into parent state.

Fields:

- `strategy`: `str`
- `source_kind`: `str`
- `carried_keys`: `list[str]`
- `parent_state_write`: `bounded_append_only | replace | none`
- `evidence_origin`: `str | None`
- `metadata`: `dict[str, Any]`

### ArtifactRef

Purpose: pointer to offloaded context, files, diffs, large tool results, or child outputs.

Fields:

- `artifact_id`: `str`
- `kind`: `tool_result | file | diff | plan | subagent_output | memory | other`
- `uri`: `str | None`
- `title`: `str | None`
- `mime_type`: `str | None`
- `size_bytes`: `int | None`
- `preview`: `str | None`
- `sensitivity`: `public | internal | confidential | secret | unknown`
- `created_at`: `str | None`
- `metadata`: `dict[str, Any]`

### UsageSummary

Purpose: normalized token/cost telemetry.

Fields:

- `input_tokens`: `int`
- `output_tokens`: `int`
- `total_tokens`: `int`
- `cache_read_tokens`: `int | None`
- `cache_creation_tokens`: `int | None`
- `cost_usd_estimate`: `float | None`
- `model_provider`: `str | None`
- `model_name`: `str | None`
- `metadata`: `dict[str, Any]`

### TraceRef

Purpose: links to external or local traces.

Fields:

- `trace_id`: `str | None`
- `span_id`: `str | None`
- `phoenix_trace_id`: `str | None`
- `langfuse_trace_id`: `str | None`
- `langsmith_trace_id`: `str | None`
- `metadata`: `dict[str, Any]`

### RunWarning

Purpose: structured warning visible to apps and evals.

Fields:

- `code`: `str`
- `message`: `str`
- `severity`: `info | warning | error`
- `source`: `runtime | model | tool | guardrail | checkpoint | eval`
- `metadata`: `dict[str, Any]`

### RedactionInfo

Purpose: document how sensitive data was handled in an event or output.

Fields:

- `applied`: `bool`
- `policy`: `str | None`
- `redacted_fields`: `list[str]`
- `sensitivity`: `public | internal | confidential | secret | unknown`
- `metadata`: `dict[str, Any]`

## Event Payload Conventions

Keep `RuntimeEvent.payload` small and typed by convention. Large content should be offloaded to `ArtifactRef`.

Examples:

```json
{
  "type": "token_delta",
  "payload": {
    "delta": "hello",
    "message_index": 0
  }
}
```

```json
{
  "type": "tool_call_started",
  "payload": {
    "tool_name": "web_fetch",
    "tool_call_id": "call_123",
    "args_summary": {
      "url": "https://example.com"
    }
  }
}
```

```json
{
  "type": "checkpoint_saved",
  "checkpoint_id": "ckpt_123",
  "payload": {
    "node_id": "tools",
    "state_version": "v1"
  }
}
```

## Serialization And Compatibility

Phase 0 should include:

- JSON schema generation for public models;
- round-trip serialization tests;
- examples for start, pause, resume, complete, and fail flows;
- stable snapshot tests for event payloads;
- compatibility policy: additive fields are allowed, renamed/removed fields require version bump.

## Minimal Phase 0 Test Matrix

Required tests before moving to Phase 1:

- `AgentRunInput` accepts input-only request.
- `AgentRunInput` accepts resume-only request.
- `AgentRunOutput(status="paused")` requires `InterruptRequest`.
- terminal output requires `terminal_reason`.
- `RuntimeEvent.seq` ordering helper produces monotonic events.
- `ResumeCommand(action="edit")` requires edit payload.
- `ToolTrace` serializes bounded args/result summaries.
- `SubagentRun` terminal validation catches incomplete rows.
- `CheckpointRef` round-trips through JSON.
- schema snapshots are generated and checked into tests or docs fixtures.

## Open Questions

- Should public models use strict Pydantic `extra="forbid"` or allow unknown top-level fields for forward compatibility?
- Should `ChatMessage.content` support typed content blocks in Phase 0 or defer until multimodal support is needed?
- Should `ArtifactRef.uri` support only engine-managed URIs first, or app-provided URIs too?
- Should `thread_id` be required for checkpointing, or can a run be durable without a conversation thread?
- Should `UsageSummary` live at run level only, or also be embedded in every LLM/tool/subagent event?

## Implementation Notes

Recommended first files:

```text
agent_driver/
  __init__.py
  contracts/
    __init__.py
    ids.py
    enums.py
    messages.py
    runtime.py
    events.py
    checkpoints.py
    interrupts.py
    tools.py
    subagents.py
    artifacts.py
    usage.py
tests/
  contracts/
    test_runtime_contracts.py
    test_event_contracts.py
    test_interrupt_contracts.py
    test_schema_snapshots.py
```

This keeps Phase 0 focused: no LangGraph dependency is required until Phase 2, unless we decide to align message/state contracts with LangGraph immediately.

## Current Implementation Mapping

Phase 0 contracts are implemented in:

- `agent_driver/contracts/base.py`
- `agent_driver/contracts/enums.py`
- `agent_driver/contracts/messages.py`
- `agent_driver/contracts/runtime.py`
- `agent_driver/contracts/events.py`
- `agent_driver/contracts/checkpoints.py`
- `agent_driver/contracts/interrupts.py`
- `agent_driver/contracts/profiles.py`
- `agent_driver/contracts/memory.py`
- `agent_driver/contracts/serialization.py`
- `agent_driver/contracts/tools.py`
- `agent_driver/contracts/subagents.py`
- `agent_driver/contracts/artifacts.py`
- `agent_driver/contracts/usage.py`
- `agent_driver/contracts/validation.py`
- `agent_driver/contracts/__init__.py` (public re-exports)

Tests:

- `tests/contracts/test_runtime_contracts.py`
- `tests/contracts/test_event_contracts.py`
- `tests/contracts/test_interrupt_contracts.py`
- `tests/contracts/test_tools_contracts.py`
- `tests/contracts/test_schema_snapshots.py`

Verification command:

```bash
.venv/bin/python -m pytest tests/contracts
```
