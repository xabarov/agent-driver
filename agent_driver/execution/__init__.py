"""Public execution-backend facade (EPIC-01).

A host injects a supported :class:`ExecutionBackend` so the built-in
``bash``/``read``/``write`` run in a prepared local or (later) remote workspace,
without changing the agent loop or governance order. The model can never select
the backend.

EPIC-01 surface: the ``ExecutionBackend`` protocol, the local compatibility
backend, a deterministic fake for tests, a composite that adapts the legacy
``AsyncCommandRunner``/``AsyncFileIO`` (for ACP), typed failures, and the
validated request/result/identity contracts (re-exported from
``agent_driver.contracts.execution``).
"""

from __future__ import annotations

from agent_driver.contracts.execution import (
    EXECUTION_SCHEMA_VERSION,
    ArtifactRef,
    CapabilitySnapshot,
    CapabilityState,
    ExecutionBounds,
    ExecutionCommandRequest,
    ExecutionCommandResult,
    ExecutionIdentity,
    ExecutionReadRequest,
    ExecutionReadResult,
    ExecutionTerminalState,
    ExecutionWriteRequest,
    ExecutionWriteResult,
)
from agent_driver.execution.adapters import (
    BackendCommandRunner,
    BackendFileIO,
    identity_from_context,
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
from agent_driver.execution.protocol import ExecutionBackend

__all__ = [
    # protocol
    "ExecutionBackend",
    # backends
    "LocalExecutionBackend",
    "FakeExecutionBackend",
    "CommandOutcome",
    "CompositeExecutionBackend",
    # adapters (backend -> legacy run-scoped seams)
    "BackendCommandRunner",
    "BackendFileIO",
    "identity_from_context",
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
    "CapabilitySnapshot",
]
