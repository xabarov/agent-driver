"""Public schema generation tests for contract models."""

from __future__ import annotations

from agent_driver.contracts import (
    AgentProfile,
    AgentRunInput,
    AgentRunOutput,
    ApprovalPayload,
    CheckpointRef,
    ExecutorSerializationPolicy,
    InterruptRequest,
    MemoryProjection,
    PromptRenderResult,
    PromptTemplate,
    ResumeCommand,
    RuntimeEvent,
    SubagentGroup,
    SubagentRun,
    ToolManifest,
    ToolResultEnvelope,
    ToolTrace,
)

PUBLIC_CONTRACT_FIELD_SNAPSHOTS: dict[str, tuple[str, ...]] = {
    "AgentRunInput": (
        "input",
        "messages",
        "thread_id",
        "run_id",
        "resume",
        "agent_id",
        "graph_preset",
        "agent_profile",
        "model_role",
        "stream",
        "prompt_template_id",
        "prompt_template_version",
        "serialization_policy",
        "tool_policy",
        "deadline_seconds",
        "max_steps",
        "max_tool_calls",
        "cost_budget_usd",
        "temperature",
        "max_tokens",
        "user_id",
        "tenant_id",
        "workspace_id",
        "app_metadata",
        "response_format",
        "tool_choice",
    ),
    "AgentRunOutput": (
        "run_id",
        "attempt_id",
        "thread_id",
        "status",
        "answer",
        "messages",
        "events",
        "tool_trace",
        "subagent_runs",
        "subagent_groups",
        "artifacts",
        "memory_projection",
        "prompt_render",
        "usage",
        "warnings",
        "trace",
        "checkpoint",
        "interrupt",
        "memory_audit",
        "terminal_reason",
        "context",
        "metadata",
    ),
    "RuntimeEvent": (
        "event_id",
        "type",
        "run_id",
        "attempt_id",
        "seq",
        "created_at",
        "checkpoint_id",
        "node_id",
        "payload",
        "trace_id",
        "severity",
        "redaction",
    ),
    "ToolManifest": (
        "name",
        "description",
        "risk",
        "side_effect",
        "approval_mode",
        "timeout_seconds",
        "output_char_budget",
        "idempotent",
        "concurrency_safe",
        "interrupt_behavior",
        "should_defer",
        "always_load",
        "aliases",
        "max_result_size_chars",
        "args_schema",
        "output_type",
        "output_schema",
        "remediation_hints",
        "supported_profiles",
        "metadata",
    ),
    "ToolTrace": (
        "step",
        "tool_name",
        "tool_call_id",
        "status",
        "args_summary",
        "result_summary",
        "artifact_refs",
        "risk",
        "side_effect",
        "approval_mode",
        "duration_ms",
        "error_code",
        "truncated",
        "metadata",
    ),
    "ToolResultEnvelope": (
        "call",
        "decision",
        "guardrail_decision",
        "summary",
        "structured_output",
        "artifacts",
        "truncated",
        "error",
        "interrupt",
        "metadata",
    ),
    "InterruptRequest": (
        "interrupt_id",
        "run_id",
        "attempt_id",
        "checkpoint_id",
        "reason",
        "title",
        "description",
        "risk",
        "proposed_action",
        "allowed_actions",
        "editable_fields",
        "proposed_prompts",
        "expires_at",
        "metadata",
    ),
    "ResumeCommand": (
        "interrupt_id",
        "action",
        "message",
        "edited_tool_args",
        "state_patch",
        "approved_by",
        "created_at",
        "approved_prompts",
        "metadata",
    ),
    "ApprovalPayload": (
        "interrupt_id",
        "reason",
        "title",
        "description",
        "risk",
        "tool_name",
        "tool_call_id",
        "args_preview",
        "allowed_actions",
        "editable_fields",
        "metadata",
    ),
}


def test_public_contract_schema_generation() -> None:
    """Ensure JSON schemas are generated for key public contracts."""
    schema = {
        "AgentRunInput": AgentRunInput.model_json_schema(),
        "AgentRunOutput": AgentRunOutput.model_json_schema(),
        "RuntimeEvent": RuntimeEvent.model_json_schema(),
        "CheckpointRef": CheckpointRef.model_json_schema(),
        "ResumeCommand": ResumeCommand.model_json_schema(),
        "InterruptRequest": InterruptRequest.model_json_schema(),
        "ToolTrace": ToolTrace.model_json_schema(),
        "SubagentRun": SubagentRun.model_json_schema(),
        "SubagentGroup": SubagentGroup.model_json_schema(),
        "PromptTemplate": PromptTemplate.model_json_schema(),
        "PromptRenderResult": PromptRenderResult.model_json_schema(),
        "MemoryProjection": MemoryProjection.model_json_schema(),
        "ExecutorSerializationPolicy": ExecutorSerializationPolicy.model_json_schema(),
        "ApprovalPayload": ApprovalPayload.model_json_schema(),
    }

    assert "properties" in schema["AgentRunInput"]
    assert "properties" in schema["AgentRunOutput"]
    assert "status" in schema["AgentRunOutput"]["properties"]
    assert "type" in schema["RuntimeEvent"]["properties"]
    assert "checkpoint_id" in schema["CheckpointRef"]["properties"]
    assert "action" in schema["ResumeCommand"]["properties"]
    assert "allowed_actions" in schema["InterruptRequest"]["properties"]
    assert "tool_name" in schema["ToolTrace"]["properties"]
    assert "terminal_state" in schema["SubagentRun"]["properties"]
    assert "join_policy" in schema["SubagentGroup"]["properties"]
    assert "profile" in schema["PromptTemplate"]["properties"]
    assert "rendered_hash" in schema["PromptRenderResult"]["properties"]
    assert "steps" in schema["MemoryProjection"]["properties"]
    assert "mode" in schema["ExecutorSerializationPolicy"]["properties"]
    assert "allowed_actions" in schema["ApprovalPayload"]["properties"]
    assert AgentProfile.REACT_TEXT.value in str(schema["PromptTemplate"])


def test_public_contract_field_snapshots() -> None:
    """Pin top-level public contract field names before SDK stabilization."""
    models = {
        "AgentRunInput": AgentRunInput,
        "AgentRunOutput": AgentRunOutput,
        "RuntimeEvent": RuntimeEvent,
        "ToolManifest": ToolManifest,
        "ToolTrace": ToolTrace,
        "ToolResultEnvelope": ToolResultEnvelope,
        "InterruptRequest": InterruptRequest,
        "ResumeCommand": ResumeCommand,
        "ApprovalPayload": ApprovalPayload,
    }

    for name, model in models.items():
        assert tuple(model.model_fields) == PUBLIC_CONTRACT_FIELD_SNAPSHOTS[name]
