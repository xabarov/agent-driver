"""Python backend factory for python tool."""

from __future__ import annotations

from agent_driver.code_agent.backends.base import PythonExecutorBackend
from agent_driver.code_agent.backends.docker import DockerPythonBackend
from agent_driver.code_agent.backends.e2b import E2BPythonBackend
from agent_driver.code_agent.backends.local import LocalPythonBackend
from agent_driver.code_agent.backends.wasm import WasmPythonBackend


def create_python_backend(mode: str, *, session_idle_seconds: float = 300.0) -> PythonExecutorBackend:
    normalized = mode.strip().lower()
    if normalized == "local":
        return LocalPythonBackend(session_idle_seconds=session_idle_seconds)
    if normalized == "docker":
        return DockerPythonBackend()
    if normalized == "e2b":
        return E2BPythonBackend()
    if normalized == "wasm":
        return WasmPythonBackend()
    raise ValueError(f"unsupported python backend '{mode}'")


__all__ = [
    "PythonExecutorBackend",
    "LocalPythonBackend",
    "DockerPythonBackend",
    "E2BPythonBackend",
    "WasmPythonBackend",
    "create_python_backend",
]
