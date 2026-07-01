"""Opt-in ``success_field`` — self-reported tool failure → FAILED trace.

Pins:
* manifest ``success_field=None`` (default) → a ``{"success": False}`` payload
  still COMPLETES (backwards compat for every existing tool);
* ``success_field`` set + field present and falsy → trace FAILED, error_code +
  result_summary lifted from the payload ``error``, envelope.error attached;
* error as a dict carries its ``code``/``message``; error as a bare string is
  used verbatim; missing error → synthesized message;
* field present and truthy → COMPLETED;
* field ABSENT from the payload → COMPLETED (a missing field never forces a
  false FAILED);
* empty ``success_field`` string is rejected at manifest construction.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts import (
    AgentRunInput,
    ApprovalMode,
    SideEffectClass,
    ToolCall,
    ToolManifest,
    ToolPolicyInput,
    ToolPolicyMode,
    ToolRisk,
)
from agent_driver.contracts.enums import ToolTraceStatus
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from tests.runtime.conftest import llm_request_with_planned_calls


def _run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="go",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def _register(registry: ToolRegistry, *, payload: dict, success_field=None) -> None:
    async def _handler(_args):
        return payload

    registry.register(
        ToolManifest(
            name="risky_tool",
            description="self-reports success",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            success_field=success_field,
        ),
        _handler,
    )


async def _run(registry: ToolRegistry, run_id: str):
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="risky_tool", args={})]
        )
    )
    return await executor.execute(_run_input(run_id), response)


# -- manifest contract ------------------------------------------------------


def test_success_field_defaults_none():
    assert ToolManifest(name="t", description="t").success_field is None


def test_empty_success_field_rejected():
    with pytest.raises(ValidationError):
        ToolManifest(name="t", description="t", success_field="  ")


# -- backwards compat -------------------------------------------------------


@pytest.mark.asyncio
async def test_no_success_field_keeps_completed_on_failure_payload():
    """Without opting in, a {"success": False} payload still COMPLETES."""
    registry = ToolRegistry()
    _register(registry, payload={"success": False, "error": "boom", "summary": "x"})
    result = await _run(registry, "r_compat")
    assert result.traces[0].status == ToolTraceStatus.COMPLETED
    assert result.envelopes[0].error is None


@pytest.mark.asyncio
async def test_completed_tool_uses_result_summary_when_summary_absent():
    """Generic handlers may expose compact facts as result_summary."""
    registry = ToolRegistry()
    _register(
        registry,
        payload={
            "result_summary": "enum_probe: Users (1): testuser; Shares (1): public",
            "output_preview": "ENUM TOOL BANNER\n" + ("raw line\n" * 300),
        },
    )

    result = await _run(registry, "r_result_summary")

    assert result.traces[0].status == ToolTraceStatus.COMPLETED
    assert result.traces[0].result_summary == "enum_probe: Users (1): testuser; Shares (1): public"
    assert result.envelopes[0].summary == "enum_probe: Users (1): testuser; Shares (1): public"


# -- opt-in failure detection ----------------------------------------------


@pytest.mark.asyncio
async def test_success_field_false_marks_failed_with_structured_error():
    registry = ToolRegistry()
    _register(
        registry,
        success_field="success",
        payload={
            "success": False,
            "error": {"code": "db_locked", "message": "row is locked"},
            "summary": "could not write",
        },
    )
    result = await _run(registry, "r_fail")
    trace = result.traces[0]
    envelope = result.envelopes[0]
    assert trace.status == ToolTraceStatus.FAILED
    assert trace.error_code == "db_locked"
    assert trace.result_summary == "row is locked"
    assert envelope.error is not None
    assert envelope.error.code == "db_locked"
    assert envelope.error.message == "row is locked"
    # The decision stays ALLOW — the tool executed; only the outcome failed.
    assert envelope.decision.value == "allow"


@pytest.mark.asyncio
async def test_success_field_false_with_string_error():
    registry = ToolRegistry()
    _register(
        registry,
        success_field="success",
        payload={"success": False, "error": "disk full", "summary": "x"},
    )
    result = await _run(registry, "r_str")
    trace = result.traces[0]
    assert trace.status == ToolTraceStatus.FAILED
    assert trace.error_code == "tool_reported_failure"
    assert trace.result_summary == "disk full"


@pytest.mark.asyncio
async def test_success_field_false_without_error_synthesizes_message():
    registry = ToolRegistry()
    _register(registry, success_field="success", payload={"success": False})
    result = await _run(registry, "r_synth")
    trace = result.traces[0]
    assert trace.status == ToolTraceStatus.FAILED
    assert trace.error_code == "tool_reported_failure"
    assert "success=False" in (trace.result_summary or "")


@pytest.mark.asyncio
async def test_success_field_true_completes():
    registry = ToolRegistry()
    _register(
        registry,
        success_field="success",
        payload={"success": True, "summary": "done"},
    )
    result = await _run(registry, "r_ok")
    assert result.traces[0].status == ToolTraceStatus.COMPLETED
    assert result.envelopes[0].error is None


@pytest.mark.asyncio
async def test_success_field_absent_completes():
    """Field declared but missing from this result → COMPLETED (conservative)."""
    registry = ToolRegistry()
    _register(registry, success_field="success", payload={"summary": "no flag here"})
    result = await _run(registry, "r_absent")
    assert result.traces[0].status == ToolTraceStatus.COMPLETED
    assert result.envelopes[0].error is None
