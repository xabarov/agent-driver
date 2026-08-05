"""EPIC-02 WP-B/C — capability-aware tool routing (pre-model + pre-dispatch).

Pre-model filtering is checked at the real request-build seam; pre-dispatch
denial is checked through the real GovernedToolExecutor (not a mock), including
the anti-TOCTOU property that dispatch re-checks the CURRENT snapshot.
"""

import pytest

from agent_driver.contracts.tools import ToolCall, ToolManifest
from agent_driver.execution import (
    CapabilityName,
    CapabilityState,
    CapabilityStatus,
    ExecutionCapabilitySnapshot,
    ToolExecutionRequirement,
)
from agent_driver.runtime.single_agent.llm_step.build import (
    _request_tools_from_registry,
)
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import capability_snapshot_scope
from tests.support.governed_tool_harness import default_run_input, execute_planned_tool


def _snapshot(command: CapabilityState) -> ExecutionCapabilitySnapshot:
    return ExecutionCapabilitySnapshot(
        backend_id="b",
        environment_revision="r1",
        capabilities={CapabilityName.COMMAND: CapabilityStatus(state=command)},
    )


def _registry_with_requirement():
    registry = ToolRegistry()
    ran: list[dict] = []

    async def _handler(args):
        ran.append(args)
        return {"summary": "ok"}

    registry.register(
        ToolManifest(
            name="needs_cmd",
            description="requires the command capability",
            execution_requirement=ToolExecutionRequirement(
                required=(CapabilityName.COMMAND,)
            ),
        ),
        _handler,
    )
    registry.register(
        ToolManifest(name="plain", description="no requirement"),
        _handler,
    )
    return registry, ran


# --------------------------------------------------------------------------- #
# pre-model filter
# --------------------------------------------------------------------------- #
def test_pre_model_withholds_tool_when_capability_unmet():
    registry, _ = _registry_with_requirement()
    schemas = _request_tools_from_registry(
        registry, capability_snapshot=_snapshot(CapabilityState.UNKNOWN)
    )
    names = {s["function"]["name"] for s in schemas}
    assert "plain" in names  # unaffected
    assert "needs_cmd" not in names  # hard requirement unmet -> withheld


def test_pre_model_exposes_tool_when_capability_supported():
    registry, _ = _registry_with_requirement()
    schemas = _request_tools_from_registry(
        registry, capability_snapshot=_snapshot(CapabilityState.SUPPORTED)
    )
    names = {s["function"]["name"] for s in schemas}
    assert {"plain", "needs_cmd"} <= names


def test_pre_model_no_snapshot_is_unaffected():
    registry, _ = _registry_with_requirement()
    schemas = _request_tools_from_registry(registry, capability_snapshot=None)
    names = {s["function"]["name"] for s in schemas}
    assert {"plain", "needs_cmd"} <= names  # no backend -> behave as before


# --------------------------------------------------------------------------- #
# pre-dispatch denial through the real GovernedToolExecutor
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_pre_dispatch_denies_when_capability_unmet_and_handler_not_run():
    registry, ran = _registry_with_requirement()
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="needs_cmd", args={}, tool_call_id="c1")

    with capability_snapshot_scope(_snapshot(CapabilityState.UNSUPPORTED)):
        result = await execute_planned_tool(
            executor, default_run_input(run_id="cap_deny"), call
        )

    assert ran == []  # governance is above dispatch: handler never ran
    env = result.envelopes[0]
    assert env.error is not None and env.error.code == "capability_unmet"


@pytest.mark.asyncio
async def test_pre_dispatch_allows_when_capability_supported():
    registry, ran = _registry_with_requirement()
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="needs_cmd", args={}, tool_call_id="c1")

    with capability_snapshot_scope(_snapshot(CapabilityState.SUPPORTED)):
        result = await execute_planned_tool(
            executor, default_run_input(run_id="cap_ok"), call
        )

    assert len(ran) == 1
    assert result.envelopes[0].error is None


@pytest.mark.asyncio
async def test_pre_dispatch_no_snapshot_runs_normally():
    # Scenario 9: default runs (no backend/snapshot) behave as before.
    registry, ran = _registry_with_requirement()
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="needs_cmd", args={}, tool_call_id="c1")

    result = await execute_planned_tool(
        executor, default_run_input(run_id="cap_none"), call
    )
    assert len(ran) == 1
    assert result.envelopes[0].error is None


@pytest.mark.asyncio
async def test_anti_toctou_dispatch_rechecks_current_snapshot():
    # A tool exposed under a supported snapshot is still denied at dispatch if
    # the snapshot has since drifted to unsupported — the re-check reads the
    # CURRENT run-scoped snapshot, not the one used to build the schema.
    registry, ran = _registry_with_requirement()

    # schema-build time: capability present -> tool would be exposed
    exposed = _request_tools_from_registry(
        registry, capability_snapshot=_snapshot(CapabilityState.SUPPORTED)
    )
    assert "needs_cmd" in {s["function"]["name"] for s in exposed}

    # dispatch time: capability drifted to unsupported -> denied
    executor = GovernedToolExecutor(registry=registry)
    call = ToolCall(tool_name="needs_cmd", args={}, tool_call_id="c1")
    with capability_snapshot_scope(_snapshot(CapabilityState.UNSUPPORTED)):
        result = await execute_planned_tool(
            executor, default_run_input(run_id="cap_drift"), call
        )
    assert ran == []
    assert result.envelopes[0].error.code == "capability_unmet"
