"""Guard tests for public exports of contracts/runtime facades."""

from __future__ import annotations

from agent_driver import contracts, runtime, sdk, tools


def test_contracts_public_exports_are_stable() -> None:
    """Contracts facade should expose key top-level models."""
    required = {
        "AgentRunInput",
        "AgentRunOutput",
        "CapabilityPackResolution",
        "EvidenceArtifactIndex",
        "EvidenceArtifactRef",
        "HarnessAdapterManifest",
        "HarnessCapabilityPack",
        "HarnessPolicyProfile",
        "HarnessReleaseGate",
        "HarnessScenarioSpec",
        "LifecycleHookCompatibilityReport",
        "LifecycleHookEvent",
        "LifecycleHookResult",
        "LifecycleMiddlewareChain",
        "McpApprovalDecision",
        "McpApprovalPolicy",
        "McpGovernanceCompatibilityReport",
        "McpRegistrySnapshot",
        "McpServerDescriptor",
        "McpToolResourceRef",
        "SkillCapabilityFilter",
        "SkillInventorySnapshot",
        "SkillLifecycleCompatibilityReport",
        "SkillLockFile",
        "SkillReloadDiff",
        "SkillSelectionDecision",
        "SkillSelectionRequest",
        "SkillUsageSummary",
        "PolicyAction",
        "PolicyEvaluation",
        "PolicySignal",
        "RunSupervisorState",
        "ToolManifest",
        "ToolTrace",
        "ValidationGateResult",
        "RuntimeEvent",
        "RunStreamEvent",
        "RunContextBudget",
        "InterruptRequest",
        "MemoryStep",
        "MemoryStepKind",
        "ResumeCommand",
        "CommandQueueItem",
        "LiveMessageCapabilities",
        "LiveMessagePhase",
        "LiveMessageSemantic",
        "NextTurnHandoff",
    }
    assert required.issubset(set(contracts.__all__))


def test_runtime_public_exports_remain_runtime_focused() -> None:
    """Runtime facade should expose runner/store symbols only."""
    required = {
        "SingleAgentRunner",
        "RunnerConfig",
        "runner_config_parameter_names",
        "InMemoryCheckpointStore",
        "InMemoryEventLog",
        "SqliteRuntimeStore",
        "RuntimeStoreFactoryConfig",
        "LifecycleMiddlewareAuditExecutor",
        "create_runtime_store_bundle",
        "wrap_governed_executor",
        "write_validation_artifacts",
        # U1 (epic 049) — host-store protocols, durable command-queue impls,
        # lifecycle-hook protocol, and run/stream projections belong on the
        # facade so embedders don't reach into runtime submodules.
        "CheckpointStore",
        "RuntimeEventLog",
        "CheckpointRecord",
        "StorageCapabilities",
        "CommandQueueStore",
        "InMemoryCommandQueueStore",
        "SqliteCommandQueueStore",
        "RunLifecycleHook",
        "BaseRunLifecycleHook",
        "RevisionRequest",
        "project_runtime_events",
        "project_run_timeline",
        "summarize_run_lifecycle",
        "RunLifecycleSnapshot",
        "resolve_run_context_budget",
        "serialize_runtime_state_for_compatibility",
        "PostgresCommandQueueStore",
        "PostgresControlStoreConfig",
        "dispatch_next_turn",
        "live_message_capabilities",
        "live_message_receipt",
    }
    forbidden = {"ToolRegistry", "GovernedToolExecutor", "SubagentGroupSpec"}
    exports = set(runtime.__all__)
    assert required.issubset(exports)
    assert forbidden.isdisjoint(exports)


def test_tools_public_exports_cover_governance_surface() -> None:
    """Tools package should own registry and governed executor exports."""
    required = {
        "ToolRegistry",
        "GovernedToolExecutor",
        "register_planning_tool",
        "custom_tool",
        "register_custom_function",
        "register_custom_tool",
        "register_mcp_tools",
        "register_skill_tools",
    }
    assert required.issubset(set(tools.__all__))


def test_sdk_public_exports_cover_app_facing_facade() -> None:
    """SDK package should expose Agent facade and factory helper."""
    required = {
        "Agent",
        "create_agent",
        "build_default_registry",
        "sdk_config_from_env",
    }
    assert required.issubset(set(sdk.__all__))
