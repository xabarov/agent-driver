"""U1 E (epic 049) — agent_driver.embedding aggregate namespace.

Every name re-exported by the aggregate must be the SAME object as on its
owning facade (identity), so the single import root can never drift from the
per-concern facades it aggregates.
"""

from __future__ import annotations

import agent_driver.contracts as contracts
import agent_driver.embedding as embedding
import agent_driver.llm as llm
import agent_driver.runtime as runtime
import agent_driver.sdk as sdk
import agent_driver.tools as tools

_OWNERS = (sdk, contracts, llm, runtime, tools)


def test_all_names_importable() -> None:
    missing = [n for n in embedding.__all__ if not hasattr(embedding, n)]
    assert not missing, missing


def test_reexports_are_identity_no_drift() -> None:
    # Each aggregated name resolves to the exact object on one owning facade.
    for name in embedding.__all__:
        obj = getattr(embedding, name)
        owners = [m for m in _OWNERS if getattr(m, name, None) is obj]
        assert owners, f"{name} is not an identity re-export of any facade"


def test_covers_the_durable_embedding_essentials() -> None:
    # The names an embedder needs to assemble the durable path (see
    # examples/cookbook/19_embedded_e2e.py) are all present.
    required = {
        "create_agent",
        "RunnerConfig",
        "runner_config_parameter_names",
        "InMemoryCheckpointStore",
        "InMemoryEventLog",
        "SqliteRuntimeStore",
        "SqliteApprovalConsumptionStore",
        "SqliteAbortLifecycleStore",
        "RunLifecycleHook",
        "BaseRunLifecycleHook",
        "RevisionRequest",
        "ToolGateAsk",
        "RunAbortHandle",
        "AgentRunInput",
        "AllowedPrompt",
        "MemoryStep",
        "MemoryStepKind",
        "RunContextBudget",
        "resolve_run_context_budget",
        "serialize_runtime_state_for_compatibility",
        "PostgresCommandQueueStore",
        "PostgresControlStoreConfig",
        "LiveMessageCapabilities",
        "LiveMessagePhase",
        "LiveMessageSemantic",
        "dispatch_next_turn",
        "live_message_capabilities",
        "FakeProvider",
        "register_skill_tools",
    }
    assert required.issubset(set(embedding.__all__))
