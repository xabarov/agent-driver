"""Guard tests for public exports of contracts/runtime facades."""

from __future__ import annotations

from agent_driver import contracts, runtime, tools
from agent_driver import sdk


def test_contracts_public_exports_are_stable() -> None:
    """Contracts facade should expose key top-level models."""
    required = {
        "AgentRunInput",
        "AgentRunOutput",
        "ToolManifest",
        "ToolTrace",
        "RuntimeEvent",
        "RunStreamEvent",
        "InterruptRequest",
        "ResumeCommand",
    }
    assert required.issubset(set(contracts.__all__))


def test_runtime_public_exports_remain_runtime_focused() -> None:
    """Runtime facade should expose runner/store symbols only."""
    required = {
        "SingleAgentRunner",
        "RunnerConfig",
        "InMemoryCheckpointStore",
        "InMemoryEventLog",
        "SqliteRuntimeStore",
        "RuntimeStoreFactoryConfig",
        "create_runtime_store_bundle",
        "wrap_governed_executor",
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
    }
    assert required.issubset(set(tools.__all__))


def test_sdk_public_exports_cover_app_facing_facade() -> None:
    """SDK package should expose Agent facade and factory helper."""
    required = {"Agent", "create_agent", "build_default_registry", "sdk_config_from_env"}
    assert required.issubset(set(sdk.__all__))
