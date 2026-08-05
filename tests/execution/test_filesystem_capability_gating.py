"""EPIC-03 WP-C — filesystem builtins are capability-gated (scenario 7).

read_file/file_write declare FILE_READ/FILE_WRITE execution requirements, so a
backend that does not report the capability withholds them pre-model and denies
them pre-dispatch (no local fallback). Default local runs (no backend/snapshot)
are unaffected.
"""

from agent_driver.contracts.execution import (
    CapabilityState,
    CapabilityStatus,
    ExecutionCapabilitySnapshot,
)
from agent_driver.runtime.single_agent.llm_step.build import (
    _request_tools_from_registry,
)
from agent_driver.tools import ToolRegistry, register_builtin_tools


def _snapshot(**caps):
    return ExecutionCapabilitySnapshot(
        backend_id="b",
        environment_revision="r1",
        capabilities={k: CapabilityStatus(state=v) for k, v in caps.items()},
    )


def _registry():
    reg = ToolRegistry()
    register_builtin_tools(reg)
    return reg


def _exposed_names(snapshot):
    schemas = _request_tools_from_registry(_registry(), capability_snapshot=snapshot)
    return {s["function"]["name"] for s in schemas}


def test_read_write_withheld_when_backend_lacks_file_capabilities():
    # backend present but reports only COMMAND -> file tools withheld
    names = _exposed_names(_snapshot(command=CapabilityState.SUPPORTED))
    assert "read_file" not in names
    assert "file_write" not in names


def test_read_write_exposed_when_capabilities_supported():
    names = _exposed_names(
        _snapshot(
            file_read=CapabilityState.SUPPORTED,
            file_write=CapabilityState.SUPPORTED,
        )
    )
    assert "read_file" in names
    assert "file_write" in names


def test_no_backend_snapshot_leaves_filesystem_tools_available():
    # default local run: no snapshot -> requirements skipped, tools present
    names = _exposed_names(None)
    assert "read_file" in names
    assert "file_write" in names


def test_unknown_capability_withholds_read_file():
    # a non-capability-aware backend resolves to all-UNKNOWN -> fail closed
    names = _exposed_names(_snapshot())  # empty caps => all UNKNOWN
    assert "read_file" not in names
