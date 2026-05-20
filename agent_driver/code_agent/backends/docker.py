"""Docker-backed python backend placeholder."""

from __future__ import annotations

from dataclasses import dataclass

from agent_driver.code_agent.contracts import CodeAgentExecutionResult, CodeAgentLimits
from agent_driver.contracts.serialization import ExecutorSerializationPolicy


@dataclass(slots=True)
class DockerPythonBackend:
    """Optional docker backend to be implemented in follow-up."""

    mode: str = "docker"

    async def execute(
        self,
        *,
        code: str,
        session_id: str,
        authorized_imports: set[str],
        limits: CodeAgentLimits,
        serialization_policy: ExecutorSerializationPolicy | None,
    ) -> CodeAgentExecutionResult:
        _ = (code, session_id, authorized_imports, limits, serialization_policy)
        raise NotImplementedError("install agent-driver[docker] and enable docker backend implementation")

    async def close_session(self, session_id: str) -> None:
        _ = session_id

    async def aclose(self) -> None:
        return None


__all__ = ["DockerPythonBackend"]
