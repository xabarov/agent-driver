"""U1 C (epic 049) — exact export snapshot for the embedding facades.

Unlike ``test_public_exports`` (which asserts required *subsets*), this pins the
EXACT ``__all__`` of the supported embedding facades. Adding or removing a public
name fails this test on purpose: a new export is a deliberate surface addition
(update the golden set + CHANGELOG), and a removal is a breaking change that must
go through the deprecation policy in docs/embedding.md. Contracts (217 models)
are intentionally excluded — their surface is guarded field-by-field by
``test_schema_snapshots`` instead.
"""

from __future__ import annotations

import agent_driver.runtime as runtime
import agent_driver.sdk as sdk
import agent_driver.tools as tools

_SDK = {
    "Agent",
    "AgentDefaults",
    "AgentDriverSDKError",
    "AsyncSubagentManager",
    "BackgroundSubagent",
    "CoordinationEvent",
    "CoordinatorPhase",
    "CoordinatorResult",
    "CustomToolDefinition",
    "DeepAgentPlan",
    "DeepAgentResult",
    "PhaseResult",
    "ProviderError",
    "ProviderErrorDetails",
    "ProviderStatusError",
    "ProviderTimeoutError",
    "ProviderTransportError",
    "RunAbortHandle",
    "RunHandle",
    "RunStream",
    "RunnerConfig",
    "SdkConfig",
    "SdkTransportConfig",
    "SelfConsistencyResult",
    "Session",
    "SubagentArtifact",
    "SubagentDigest",
    "SubagentGroupResult",
    "SubagentJoinPolicy",
    "SubagentMergeMode",
    "SubagentLimits",
    "SubagentModelPolicy",
    "SubagentOutputPolicy",
    "SubagentResult",
    "SubagentSpec",
    "SubagentToolPolicy",
    "VerifierVerdict",
    "ToolRegistry",
    "agent_as_tool",
    "ToolSet",
    "TraceSummary",
    "ValueToAction",
    "artifact_references",
    "build_default_registry",
    "handoff_tool",
    "capture_group_artifacts",
    "capture_subagent_artifact",
    "create_agent",
    "describe",
    "describe_coordinator",
    "describe_deep_agent",
    "describe_group",
    "describe_subagent",
    "digest_subagent",
    "fork_subagent",
    "interrupt_to_stream_event",
    "log_coordination_events",
    "merge_subagent_results",
    "query",
    "synthesize_subagent_results",
    "register_custom_function",
    "resume_command_from_payload",
    "run_coordinator",
    "run_deep_agent",
    "run_self_consistent",
    "run_subagent",
    "run_subagent_group",
    "sdk_config_from_env",
    "share_workspace",
    "summarize_output",
    "support_bundle",
    "tool",
    "verify_answer",
    "verify_subagent_group",
    "verify_subagent_result",
}

_RUNTIME = {
    "AbortLifecycleState",
    "AbortLifecycleStore",
    "AbortRecord",
    "ApprovalConsumptionStore",
    "BaseRunLifecycleHook",
    "CapabilitySettings",
    "CompactionSettings",
    "TrimmingSettings",
    "CheckpointRecord",
    "CheckpointStore",
    "CommandQueueStore",
    "DeferPrimer",
    "DeferPrimerInput",
    "ExecutionProof",
    "FakeSingleStepRunner",
    "FallbackSpec",
    "GateProvenance",
    "GraderVerdict",
    "HookChainExecutor",
    "HookChainLifecycleHook",
    "InMemoryAbortLifecycleStore",
    "InMemoryApprovalConsumptionStore",
    "InMemoryCheckpointStore",
    "InMemoryCommandQueueStore",
    "InMemoryEventLog",
    "ROLLBACK_TARGET_0_2_RC5",
    "LifecycleHookExecution",
    "LifecycleMiddlewareAuditExecutor",
    "MissingCheckpointError",
    "PLANNING_TOOL_NAMES",
    "POSTGRES_CAPABILITIES",
    "POSTGRES_SCHEMA_VERSION",
    "PostgresAbortLifecycleStore",
    "PostgresApprovalConsumptionStore",
    "PostgresCommandQueueStore",
    "PostgresControlStoreConfig",
    "PostgresPlanArtifactStore",
    "PostgresRuntimeStore",
    "PostgresRuntimeStoreConfig",
    "RubricGradeInput",
    "RubricLifecycleHook",
    "RubricRuntimeState",
    "get_rubric_runtime_state",
    "RevisionRequest",
    "RunAbortHandle",
    "RunLifecycleHook",
    "RunLifecycleSnapshot",
    "RunLifecycleState",
    "RunTimelineRow",
    "RunnerConfig",
    "runner_config_parameter_names",
    "RuntimeEventLog",
    "RuntimeExecutionError",
    "RuntimeSessionDiagnostics",
    "RuntimeState",
    "RuntimeStateCompatibilityResult",
    "RuntimeStepResult",
    "RuntimeStoreBundle",
    "RuntimeStoreFactoryConfig",
    "RuntimeStorePreflightResult",
    "SingleAgentRunner",
    "SqliteAbortLifecycleStore",
    "SqliteApprovalConsumptionStore",
    "SqliteCommandQueueStore",
    "SqliteRuntimeStore",
    "StorageCapabilities",
    "ToolExecutionResult",
    "ToolExecutor",
    "ToolGate",
    "ToolGateAllow",
    "ToolGateAsk",
    "ToolGateContext",
    "ToolGateDeny",
    "ToolGateResult",
    "backfill_stream_events",
    "create_runtime_store_bundle",
    "data_tool_called",
    "fake_noop_tool_executor",
    "has_real_execution_proof",
    "keyword_relevance_primer",
    "dispatch_next_turn",
    "live_message_capabilities",
    "live_message_receipt",
    "live_message_transition_event",
    "placeholders_for_event",
    "planning_executed",
    "planning_executed_across",
    "planning_tool_called",
    "preflight_runtime_store",
    "project_run_timeline",
    "project_runtime_events",
    "tool_name_from_event",
    "requires_guardrails_after_transform",
    "result_from_existing_hook_output",
    "runtime_store_config_from_env",
    "resolve_run_context_budget",
    "serialize_runtime_state_for_compatibility",
    "summarize_execution_proof",
    "summarize_run_lifecycle",
    "wrap_governed_executor",
    "write_validation_artifacts",
}

_TOOLS = {
    "AntipatternMatch",
    "AntipatternRule",
    "ContractHandler",
    "CustomToolDefinition",
    "GovernedToolExecutor",
    "GuardrailPipeline",
    "GuardrailResult",
    "PreferenceRule",
    "PromptTemplateRegistry",
    "RegisteredTool",
    "ToolChoiceContext",
    "ToolChoicePolicyRegistry",
    "ToolChoiceScore",
    "ToolRegistry",
    "ToolSet",
    "antipattern_to_warning_payload",
    "apply_planning_state_tool_update",
    "assemble_tool_pool",
    "build_default_tool_choice_registry",
    "builtin_pack_names",
    "custom_tool",
    "empty_result_marker",
    "evaluate_tool_policy",
    "generic_after_specialized_search",
    "get_merged_tools",
    "is_truncated",
    "manifest_from_contract",
    "persisted_output_envelope",
    "planning_state_update_tool",
    "prefer_specialized_over_generic",
    "register_builtin_tools",
    "register_contract_tool",
    "register_custom_function",
    "register_custom_tool",
    "register_mcp_tools",
    "register_memory_tool",
    "register_planning_tool",
    "register_skill_tools",
    "render_tool_doc",
    "render_tool_docs",
    "rendered_tool_docs_hash",
    "safe_preview",
    "tool",
    "tool_from_function",
}


def _diff(actual: set[str], golden: set[str]) -> str:
    return (
        f"added={sorted(actual - golden)} removed={sorted(golden - actual)} "
        "— update the golden set in this test + CHANGELOG (deprecation policy for "
        "removals, see docs/embedding.md)"
    )


def test_sdk_export_snapshot() -> None:
    actual = set(sdk.__all__)
    assert actual == _SDK, _diff(actual, _SDK)


def test_runtime_export_snapshot() -> None:
    actual = set(runtime.__all__)
    assert actual == _RUNTIME, _diff(actual, _RUNTIME)


def test_tools_export_snapshot() -> None:
    actual = set(tools.__all__)
    assert actual == _TOOLS, _diff(actual, _TOOLS)


def test_all_snapshot_names_are_importable() -> None:
    for mod, golden in ((sdk, _SDK), (runtime, _RUNTIME), (tools, _TOOLS)):
        for name in golden:
            assert hasattr(mod, name), f"{mod.__name__}.{name} missing"
