"""App-facing SDK facade for run/stream/resume ergonomics."""

# Identity re-exports (SDK S2): the primary build path is `create_agent` (here) +
# a `RunnerConfig`, and per-run control is `agent.run(..., abort_handle=...)` with a
# `RunAbortHandle`. Both used to live only on `agent_driver.runtime`, forcing the
# two halves of one operation across two facades; re-export them so the build/run
# path is a single import. Same objects as `agent_driver.runtime.*` (no drift).
from agent_driver.runtime import RunAbortHandle, RunnerConfig
from agent_driver.sdk.agent import Agent, AgentDefaults
from agent_driver.sdk.config import SdkConfig, SdkTransportConfig
from agent_driver.sdk.errors import (
    AgentDriverSDKError,
    ProviderError,
    ProviderErrorDetails,
    ProviderStatusError,
    ProviderTimeoutError,
    ProviderTransportError,
)
from agent_driver.sdk.factory import (
    build_default_registry,
    create_agent,
    query,
    sdk_config_from_env,
)
from agent_driver.sdk.handle import RunHandle, RunStream
from agent_driver.sdk.resume_payload import (
    ValueToAction,
    interrupt_to_stream_event,
    resume_command_from_payload,
)
from agent_driver.sdk.session import Session
from agent_driver.sdk.async_subagent import AsyncSubagentManager, BackgroundSubagent
from agent_driver.sdk.fork import fork_subagent
from agent_driver.sdk.subagent import (
    SubagentLimits,
    SubagentModelPolicy,
    SubagentOutputPolicy,
    SubagentResult,
    SubagentSpec,
    SubagentToolPolicy,
    run_subagent,
)
from agent_driver.contracts.enums import SubagentJoinPolicy, SubagentMergeMode
from agent_driver.sdk.group import SubagentGroupResult, run_subagent_group
from agent_driver.sdk.merge import (
    merge_subagent_results,
    synthesize_subagent_results,
)
from agent_driver.sdk.coordinator import (
    CoordinatorPhase,
    CoordinatorResult,
    PhaseResult,
    run_coordinator,
)
from agent_driver.sdk.artifacts import (
    SubagentArtifact,
    artifact_references,
    capture_group_artifacts,
    capture_subagent_artifact,
    share_workspace,
)
from agent_driver.sdk.deep_agent import (
    DeepAgentPlan,
    DeepAgentResult,
    run_deep_agent,
)
from agent_driver.sdk.verify import (
    VerifierVerdict,
    verify_answer,
    verify_subagent_group,
    verify_subagent_result,
)
from agent_driver.sdk.agent_tool import agent_as_tool, handoff_tool
from agent_driver.sdk.coordination_trace import (
    SubagentDigest,
    describe,
    describe_coordinator,
    describe_deep_agent,
    describe_group,
    describe_subagent,
    digest_subagent,
)
from agent_driver.sdk.self_consistency import (
    SelfConsistencyResult,
    run_self_consistent,
)
from agent_driver.sdk.trace import TraceSummary, summarize_output, support_bundle
from agent_driver.tools import (
    CustomToolDefinition,
    ToolRegistry,
    ToolSet,
    register_custom_function,
    tool,
)

__all__ = [
    "Agent",
    "AgentDefaults",
    "RunAbortHandle",
    "RunnerConfig",
    "SelfConsistencyResult",
    "run_self_consistent",
    "AsyncSubagentManager",
    "BackgroundSubagent",
    "CoordinatorPhase",
    "CoordinatorResult",
    "DeepAgentPlan",
    "DeepAgentResult",
    "run_deep_agent",
    "PhaseResult",
    "run_coordinator",
    "SubagentArtifact",
    "artifact_references",
    "capture_group_artifacts",
    "capture_subagent_artifact",
    "share_workspace",
    "VerifierVerdict",
    "verify_answer",
    "verify_subagent_group",
    "verify_subagent_result",
    "agent_as_tool",
    "handoff_tool",
    "SubagentDigest",
    "describe",
    "describe_coordinator",
    "describe_deep_agent",
    "describe_group",
    "describe_subagent",
    "digest_subagent",
    "AgentDriverSDKError",
    "ProviderError",
    "ProviderErrorDetails",
    "ProviderStatusError",
    "ProviderTimeoutError",
    "ProviderTransportError",
    "RunHandle",
    "RunStream",
    "SdkConfig",
    "SdkTransportConfig",
    "Session",
    "SubagentGroupResult",
    "SubagentJoinPolicy",
    "SubagentMergeMode",
    "SubagentLimits",
    "SubagentModelPolicy",
    "SubagentOutputPolicy",
    "SubagentResult",
    "SubagentSpec",
    "SubagentToolPolicy",
    "run_subagent_group",
    "TraceSummary",
    "ToolSet",
    "ToolRegistry",
    "CustomToolDefinition",
    "register_custom_function",
    "tool",
    "ValueToAction",
    "build_default_registry",
    "create_agent",
    "fork_subagent",
    "interrupt_to_stream_event",
    "merge_subagent_results",
    "query",
    "synthesize_subagent_results",
    "resume_command_from_payload",
    "run_subagent",
    "sdk_config_from_env",
    "summarize_output",
    "support_bundle",
]
