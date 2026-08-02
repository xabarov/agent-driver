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
    "Agent", "AgentDefaults", "AgentDriverSDKError", "AsyncSubagentManager",
    "BackgroundSubagent", "CustomToolDefinition", "ProviderError",
    "ProviderErrorDetails", "ProviderStatusError", "ProviderTimeoutError",
    "ProviderTransportError", "RunHandle", "RunStream", "SdkConfig",
    "SdkTransportConfig", "SelfConsistencyResult", "Session", "SubagentLimits",
    "SubagentOutputPolicy", "SubagentResult", "SubagentSpec", "SubagentToolPolicy",
    "ToolRegistry", "ToolSet", "TraceSummary", "ValueToAction",
    "build_default_registry", "create_agent", "fork_subagent",
    "interrupt_to_stream_event", "query", "register_custom_function",
    "resume_command_from_payload", "run_self_consistent", "run_subagent",
    "sdk_config_from_env", "summarize_output", "support_bundle", "tool",
}

_RUNTIME = {
    "AbortLifecycleState", "AbortLifecycleStore", "AbortRecord",
    "ApprovalConsumptionStore", "BaseRunLifecycleHook", "CapabilitySettings",
    "CheckpointRecord", "CheckpointStore", "CommandQueueStore", "DeferPrimer",
    "DeferPrimerInput", "ExecutionProof", "FakeSingleStepRunner", "FallbackSpec",
    "GateProvenance", "GraderVerdict", "HookChainExecutor",
    "HookChainLifecycleHook", "InMemoryAbortLifecycleStore",
    "InMemoryApprovalConsumptionStore", "InMemoryCheckpointStore",
    "InMemoryCommandQueueStore", "InMemoryEventLog", "LifecycleHookExecution",
    "LifecycleMiddlewareAuditExecutor", "MissingCheckpointError",
    "PLANNING_TOOL_NAMES", "POSTGRES_CAPABILITIES", "POSTGRES_SCHEMA_VERSION",
    "PostgresRuntimeStore", "PostgresRuntimeStoreConfig", "RubricGradeInput",
    "RubricLifecycleHook", "RunAbortHandle", "RunLifecycleHook",
    "RunLifecycleSnapshot", "RunLifecycleState", "RunTimelineRow", "RunnerConfig",
    "RuntimeEventLog", "RuntimeExecutionError", "RuntimeSessionDiagnostics",
    "RuntimeState", "RuntimeStepResult", "RuntimeStoreBundle",
    "RuntimeStoreFactoryConfig", "RuntimeStorePreflightResult",
    "SingleAgentRunner", "SqliteAbortLifecycleStore",
    "SqliteApprovalConsumptionStore", "SqliteCommandQueueStore",
    "SqliteRuntimeStore", "StorageCapabilities", "ToolExecutionResult",
    "ToolExecutor", "ToolGate", "ToolGateAllow", "ToolGateAsk", "ToolGateContext",
    "ToolGateDeny", "ToolGateResult", "backfill_stream_events",
    "create_runtime_store_bundle", "data_tool_called", "fake_noop_tool_executor",
    "has_real_execution_proof", "keyword_relevance_primer",
    "placeholders_for_event", "planning_executed", "planning_executed_across",
    "planning_tool_called", "preflight_runtime_store", "project_run_timeline",
    "project_runtime_events", "requires_guardrails_after_transform",
    "result_from_existing_hook_output", "runtime_store_config_from_env",
    "summarize_execution_proof", "summarize_run_lifecycle",
    "wrap_governed_executor", "write_validation_artifacts",
}

_TOOLS = {
    "AntipatternMatch", "AntipatternRule", "ContractHandler",
    "CustomToolDefinition", "GovernedToolExecutor", "GuardrailPipeline",
    "GuardrailResult", "PreferenceRule", "PromptTemplateRegistry",
    "RegisteredTool", "ToolChoiceContext", "ToolChoicePolicyRegistry",
    "ToolChoiceScore", "ToolRegistry", "ToolSet",
    "antipattern_to_warning_payload", "apply_planning_state_tool_update",
    "assemble_tool_pool", "build_default_tool_choice_registry",
    "builtin_pack_names", "custom_tool", "empty_result_marker",
    "evaluate_tool_policy", "generic_after_specialized_search",
    "get_merged_tools", "is_truncated", "manifest_from_contract",
    "persisted_output_envelope", "planning_state_update_tool",
    "prefer_specialized_over_generic", "register_builtin_tools",
    "register_contract_tool", "register_custom_function", "register_custom_tool",
    "register_mcp_tools", "register_planning_tool", "render_tool_doc",
    "render_tool_docs", "rendered_tool_docs_hash", "safe_preview", "tool",
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
