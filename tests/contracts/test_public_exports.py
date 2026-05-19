"""Guard tests for public exports of contracts/runtime facades."""

from __future__ import annotations

from agent_driver import contracts, runtime, tools


def test_contracts_public_exports_are_stable() -> None:
    """Contracts facade should expose key top-level models."""
    required = {
        "AgentRunInput",
        "AgentRunOutput",
        "ToolManifest",
        "ToolTrace",
        "RuntimeEvent",
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
    required = {"ToolRegistry", "GovernedToolExecutor", "register_planning_tool"}
    assert required.issubset(set(tools.__all__))
