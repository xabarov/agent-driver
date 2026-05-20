"""Python executor backend protocol for python tool."""

from __future__ import annotations

from typing import Protocol

from agent_driver.code_agent.contracts import CodeAgentExecutionResult, CodeAgentLimits
from agent_driver.contracts.serialization import ExecutorSerializationPolicy


class PythonExecutorBackend(Protocol):
    """Backend contract for executing python snippets."""

    mode: str

    async def execute(
        self,
        *,
        code: str,
        session_id: str,
        authorized_imports: set[str],
        limits: CodeAgentLimits,
        serialization_policy: ExecutorSerializationPolicy | None,
    ) -> CodeAgentExecutionResult:
        """Execute one snippet and return normalized payload."""
        raise NotImplementedError

    async def close_session(self, session_id: str) -> None:
        """Drop one executor session if backend supports stateful sessions."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Release all backend resources."""
        raise NotImplementedError


__all__ = ["PythonExecutorBackend"]
