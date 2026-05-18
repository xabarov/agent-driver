"""CodeAgent package facade (Phase 7)."""

from agent_driver.code_agent.contracts import (
    CodeAgentAction,
    CodeAgentExecutionResult,
    CodeAgentFinalAnswer,
    CodeAgentLimits,
    CodeAgentObservation,
)
from agent_driver.code_agent.executor import (
    CodeActionExecutor,
    CodeExecutionError,
    FakeRestrictedCodeExecutor,
)
from agent_driver.code_agent.policy import PolicyViolation, validate_code_action
from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.code_agent.serialization import deserialize_payload, serialize_payload
from agent_driver.code_agent.tool_surface import (
    CallableToolSpec,
    build_callable_tool_surface,
    callable_signature_map,
    render_callable_tool_docs,
)

__all__ = [
    "CodeAgentAction",
    "CodeAgentExecutionResult",
    "CodeAgentFinalAnswer",
    "CodeAgentLimits",
    "CodeAgentObservation",
    "CodeActionExecutor",
    "CodeExecutionError",
    "FakeRestrictedCodeExecutor",
    "PolicyViolation",
    "validate_code_action",
    "run_code_agent_stage",
    "serialize_payload",
    "deserialize_payload",
    "CallableToolSpec",
    "build_callable_tool_surface",
    "callable_signature_map",
    "render_callable_tool_docs",
]
