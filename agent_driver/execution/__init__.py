"""Public execution-backend facade (EPIC-01, EPIC-02).

A host injects a supported :class:`ExecutionBackend` so the built-in
``bash``/``read``/``write`` run in a prepared local or (later) remote workspace,
without changing the agent loop or governance order. The model can never select
the backend.

EPIC-01 surface: the ``ExecutionBackend`` protocol, the local compatibility
backend, a deterministic fake for tests, a composite that adapts the legacy
``AsyncCommandRunner``/``AsyncFileIO`` (for ACP), typed failures, and the
validated request/result/identity contracts (re-exported from
``agent_driver.contracts.execution``).

EPIC-02 adds capability truth: the optional ``CapabilityAwareBackend`` protocol,
the ``ExecutionCapabilitySnapshot``/``EnvironmentBrief``/``ToolExecutionRequirement``
contracts, and the deterministic ``resolve_capability_snapshot`` /
``check_requirement`` / ``derive_environment_brief`` routing helpers.
"""

from __future__ import annotations

from agent_driver.contracts.execution import (
    EXECUTION_CAPABILITY_SCHEMA_VERSION,
    EXECUTION_SCHEMA_VERSION,
    ArtifactRef,
    CapabilityName,
    CapabilityState,
    CapabilityStatus,
    EnvironmentBrief,
    ExecutionBounds,
    ExecutionCapabilitySnapshot,
    ExecutionCommandRequest,
    ExecutionCommandResult,
    ExecutionIdentity,
    ExecutionReadRequest,
    ExecutionReadResult,
    ExecutionTerminalState,
    ExecutionWriteRequest,
    ExecutionWriteResult,
    ProgramInfo,
    RequirementCheck,
    ToolExecutionRequirement,
)
from agent_driver.execution.adapters import (
    BackendCommandRunner,
    BackendFileIO,
    identity_from_context,
)
from agent_driver.execution.capabilities import (
    DEFAULT_BRIEF_MAX_CHARS,
    capability_diagnostics,
    check_manifest_requirement,
    check_requirement,
    derive_environment_brief,
    render_environment_brief_text,
    resolve_capability_snapshot,
    tool_is_withheld,
    unknown_snapshot,
)
from agent_driver.execution.composite import CompositeExecutionBackend
from agent_driver.execution.errors import (
    BackendProtocolError,
    ExecutionError,
    ExecutionTimeoutError,
    ExecutionTransportError,
    IndeterminateExecutionError,
    OutputLimitExceededError,
    UnsupportedCapabilityError,
)
from agent_driver.execution.fake import CommandOutcome, FakeExecutionBackend
from agent_driver.execution.local import LocalExecutionBackend
from agent_driver.execution.protocol import CapabilityAwareBackend, ExecutionBackend

__all__ = [
    # protocols
    "ExecutionBackend",
    "CapabilityAwareBackend",
    # backends
    "LocalExecutionBackend",
    "FakeExecutionBackend",
    "CommandOutcome",
    "CompositeExecutionBackend",
    # adapters (backend -> legacy run-scoped seams)
    "BackendCommandRunner",
    "BackendFileIO",
    "identity_from_context",
    # capability routing helpers
    "resolve_capability_snapshot",
    "unknown_snapshot",
    "check_requirement",
    "check_manifest_requirement",
    "tool_is_withheld",
    "derive_environment_brief",
    "render_environment_brief_text",
    "capability_diagnostics",
    "DEFAULT_BRIEF_MAX_CHARS",
    # errors
    "ExecutionError",
    "UnsupportedCapabilityError",
    "ExecutionTimeoutError",
    "ExecutionTransportError",
    "IndeterminateExecutionError",
    "OutputLimitExceededError",
    "BackendProtocolError",
    # contracts (re-exported)
    "EXECUTION_SCHEMA_VERSION",
    "EXECUTION_CAPABILITY_SCHEMA_VERSION",
    "ExecutionTerminalState",
    "CapabilityState",
    "ExecutionIdentity",
    "ExecutionBounds",
    "ArtifactRef",
    "ExecutionCommandRequest",
    "ExecutionCommandResult",
    "ExecutionReadRequest",
    "ExecutionReadResult",
    "ExecutionWriteRequest",
    "ExecutionWriteResult",
    # capability contracts
    "CapabilityName",
    "CapabilityStatus",
    "ProgramInfo",
    "ExecutionCapabilitySnapshot",
    "ToolExecutionRequirement",
    "RequirementCheck",
    "EnvironmentBrief",
]
