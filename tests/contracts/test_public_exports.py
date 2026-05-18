"""Guard tests for public exports of contracts/runtime facades."""

from __future__ import annotations

from agent_driver import contracts, runtime


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
    """Runtime facade should expose core runner/store/governance bridge symbols."""
    required = {
        "SingleAgentRunner",
        "RunnerConfig",
        "InMemoryCheckpointStore",
        "InMemoryEventLog",
        "SqliteRuntimeStore",
        "RuntimeStoreFactoryConfig",
        "create_runtime_store_bundle",
        "wrap_governed_executor",
        "ToolRegistry",
        "GovernedToolExecutor",
    }
    assert required.issubset(set(runtime.__all__))
