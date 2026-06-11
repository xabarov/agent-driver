"""CodeAgent package facade (Phase 7)."""

from agent_driver.code_agent.contracts import (
    CodeAgentAction,
    CodeAgentExecutionResult,
    CodeAgentFinalAnswer,
    CodeAgentLimits,
    CodeAgentObservation,
)
from agent_driver.code_agent.backends import (
    create_python_backend,
    DockerPythonBackend,
    E2BPythonBackend,
    LocalPythonBackend,
    PythonExecutorBackend,
    WasmPythonBackend,
)
from agent_driver.code_agent.executor import (
    CodeActionExecutor,
    CodeExecutionError,
    FakeRestrictedCodeExecutor,
)
from agent_driver.code_agent.subprocess_executor import SubprocessRestrictedCodeExecutor
from agent_driver.code_agent.sandbox import (
    DEFAULT_RESULT_VARS,
    run_sandboxed,
    SandboxError,
    SandboxLimits,
    SandboxPolicyError,
    SandboxResult,
    SandboxTimeoutError,
)
from agent_driver.code_agent.policy import PolicyViolation, validate_code_action
from agent_driver.code_agent.profile import run_code_agent_stage
from agent_driver.code_agent.prompt import render_code_agent_prompt
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
    "SubprocessRestrictedCodeExecutor",
    "PolicyViolation",
    "validate_code_action",
    "run_code_agent_stage",
    "render_code_agent_prompt",
    "serialize_payload",
    "deserialize_payload",
    "CallableToolSpec",
    "build_callable_tool_surface",
    "callable_signature_map",
    "render_callable_tool_docs",
    "PythonExecutorBackend",
    "LocalPythonBackend",
    "DockerPythonBackend",
    "E2BPythonBackend",
    "WasmPythonBackend",
    "create_python_backend",
    "run_sandboxed",
    "SandboxLimits",
    "SandboxResult",
    "SandboxError",
    "SandboxTimeoutError",
    "SandboxPolicyError",
    "DEFAULT_RESULT_VARS",
]
