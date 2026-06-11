"""Library-readiness guard: py.typed ships + documented entry points import."""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest


def test_py_typed_marker_present() -> None:
    import agent_driver

    marker = Path(agent_driver.__file__).parent / "py.typed"
    assert marker.is_file(), "PEP 561 py.typed marker must ship for a typed SDK"


# (module, names that must be importable) — mirrors docs/embedding.md.
_PUBLIC_SURFACE = {
    "agent_driver.sdk": (
        "create_agent",
        "query",
        "Agent",
        "Session",
        "ToolSet",
        "run_subagent",
        "SubagentSpec",
        "AsyncSubagentManager",
        "fork_subagent",
    ),
    "agent_driver.runtime": ("RunnerConfig", "CapabilitySettings", "RunAbortHandle"),
    "agent_driver.contracts": ("AgentRunInput", "AgentRunOutput", "HarnessProfile"),
    "agent_driver.llm": (
        "FakeProvider",
        "resolve_provider",
        "sanitize_request_messages",
    ),
    "agent_driver.permissions": (
        "PermissionPolicy",
        "PermissionRule",
        "build_permission_gate",
    ),
    "agent_driver.memory": ("MemoryProvider", "StoreBackedMemoryProvider"),
    "agent_driver.fs": (
        "FileBackend",
        "StateBackend",
        "LocalFilesystemBackend",
        "CompositeBackend",
    ),
    "agent_driver.harness": ("select_harness_profile", "apply_system_slots"),
    "agent_driver.batch": ("BatchRunner", "Trajectory", "compress_trajectory"),
    "agent_driver.evals": (
        "run_comparison",
        "aggregate_trajectories",
        "general_task_suite",
    ),
    "agent_driver.scheduler": ("Scheduler", "ScheduledJob"),
    "agent_driver.security": ("scan_context_text",),
}


@pytest.mark.parametrize("module_name,names", sorted(_PUBLIC_SURFACE.items()))
def test_documented_entry_points_import(
    module_name: str, names: tuple[str, ...]
) -> None:
    module = importlib.import_module(module_name)
    missing = [name for name in names if not hasattr(module, name)]
    assert not missing, f"{module_name} is missing documented exports: {missing}"
