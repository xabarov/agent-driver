"""One aggregate namespace for host embedders (U1 / epic 049).

The supported public surface is spread across the per-concern facades
(``agent_driver.sdk`` / ``.runtime`` / ``.llm`` / ``.contracts`` / ``.tools``),
each documented in ``docs/embedding.md``. This module re-exports the
embedding-essential names from those facades under a single import root, so a
host can write ``from agent_driver.embedding import create_agent, RunnerConfig,
SqliteRuntimeStore, ToolGateAsk, RunAbortHandle`` instead of tracking which
facade owns each name.

It adds no new API: every name here is the SAME object as on its owning facade
(``agent_driver.embedding.create_agent is agent_driver.sdk.create_agent``), so
there is nothing to drift. For the full, exhaustive surface use the owning
facades directly.
"""

from __future__ import annotations

# — App construction / run lifecycle (agent_driver.sdk) —
from agent_driver.sdk import (
    Agent,
    RunHandle,
    RunStream,
    Session,
    ToolSet,
    create_agent,
    query,
    resume_command_from_payload,
)

# — Wire contracts (agent_driver.contracts) —
from agent_driver.contracts import (
    AgentRunInput,
    AgentRunOutput,
    InterruptRequest,
    ResumeCommand,
)

# — Provider protocol + built-ins (agent_driver.llm) —
from agent_driver.llm import (
    FakeProvider,
    LlmProvider,
    OllamaProvider,
    OpenAICompatibleProvider,
    resolve_provider,
)

# — Runner config, host stores, hooks, gate, abort (agent_driver.runtime) —
from agent_driver.runtime import (
    AbortLifecycleStore,
    ApprovalConsumptionStore,
    BaseRunLifecycleHook,
    CapabilitySettings,
    CheckpointRecord,
    CheckpointStore,
    CommandQueueStore,
    GateProvenance,
    InMemoryAbortLifecycleStore,
    InMemoryApprovalConsumptionStore,
    InMemoryCheckpointStore,
    InMemoryCommandQueueStore,
    InMemoryEventLog,
    PostgresRuntimeStore,
    RunAbortHandle,
    RunLifecycleHook,
    RunnerConfig,
    RuntimeEventLog,
    SqliteAbortLifecycleStore,
    SqliteApprovalConsumptionStore,
    SqliteCommandQueueStore,
    SqliteRuntimeStore,
    ToolGate,
    ToolGateAllow,
    ToolGateAsk,
    ToolGateContext,
    ToolGateDeny,
    ToolGateResult,
    project_run_timeline,
    project_runtime_events,
    summarize_run_lifecycle,
    wrap_governed_executor,
)

# — Tool registry + custom tools (agent_driver.tools) —
from agent_driver.tools import (
    GovernedToolExecutor,
    ToolRegistry,
    custom_tool,
    register_custom_function,
    tool,
)

__all__ = [
    # sdk
    "Agent",
    "RunHandle",
    "RunStream",
    "Session",
    "ToolSet",
    "create_agent",
    "query",
    "resume_command_from_payload",
    # contracts
    "AgentRunInput",
    "AgentRunOutput",
    "InterruptRequest",
    "ResumeCommand",
    # llm
    "FakeProvider",
    "LlmProvider",
    "OllamaProvider",
    "OpenAICompatibleProvider",
    "resolve_provider",
    # runtime
    "AbortLifecycleStore",
    "ApprovalConsumptionStore",
    "BaseRunLifecycleHook",
    "CapabilitySettings",
    "CheckpointRecord",
    "CheckpointStore",
    "CommandQueueStore",
    "GateProvenance",
    "InMemoryAbortLifecycleStore",
    "InMemoryApprovalConsumptionStore",
    "InMemoryCheckpointStore",
    "InMemoryCommandQueueStore",
    "InMemoryEventLog",
    "PostgresRuntimeStore",
    "RunAbortHandle",
    "RunLifecycleHook",
    "RunnerConfig",
    "RuntimeEventLog",
    "SqliteAbortLifecycleStore",
    "SqliteApprovalConsumptionStore",
    "SqliteCommandQueueStore",
    "SqliteRuntimeStore",
    "ToolGate",
    "ToolGateAllow",
    "ToolGateAsk",
    "ToolGateContext",
    "ToolGateDeny",
    "ToolGateResult",
    "project_run_timeline",
    "project_runtime_events",
    "summarize_run_lifecycle",
    "wrap_governed_executor",
    # tools
    "GovernedToolExecutor",
    "ToolRegistry",
    "custom_tool",
    "register_custom_function",
    "tool",
]
