"""Epic 033+: a deferred tool called without required args returns its schema."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import ApprovalMode, SideEffectClass, ToolRisk
from agent_driver.contracts.tools import ToolCall, ToolManifest
from agent_driver.tools import GovernedToolExecutor, ToolRegistry


def _registry(*, deferred: bool) -> tuple[ToolRegistry, dict]:
    calls: dict = {"n": 0}

    async def _handler(args):
        calls["n"] += 1
        return {"ok": True, "got": args}

    registry = ToolRegistry()
    registry.register(
        ToolManifest(
            name="deep_probe",
            description="A deferred lookup tool",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            should_defer=deferred,
            args_schema={
                "type": "object",
                "properties": {"target": {"type": "string"}},
                "required": ["target"],
            },
        ),
        _handler,
    )
    return registry, calls


async def _run_call(registry: ToolRegistry, args: dict):
    from agent_driver.contracts import AgentRunInput
    from agent_driver.contracts.messages import ChatMessage
    from agent_driver.contracts.usage import UsageSummary
    from agent_driver.llm.contracts import LlmFinishReason, LlmResponse

    executor = GovernedToolExecutor(registry=registry)
    response = LlmResponse(
        message=ChatMessage(role="assistant", content=""),
        finish_reason=LlmFinishReason.TOOL_CALLS,
        usage=UsageSummary(model_provider="fake", model_name="m"),
        provider="fake",
        model="m",
        metadata={
            "planned_tool_calls": [
                ToolCall(tool_name="deep_probe", tool_call_id="c1", args=args).model_dump(
                    mode="json"
                )
            ]
        },
    )
    run_input = AgentRunInput(
        input="x", run_id="r", agent_id="a", graph_preset="single_react"
    )
    return await executor.execute(run_input, response)


@pytest.mark.asyncio
async def test_deferred_missing_required_returns_schema_without_dispatch() -> None:
    registry, calls = _registry(deferred=True)
    result = await _run_call(registry, {})  # missing required "target"
    assert calls["n"] == 0  # handler NOT dispatched blind
    envelope = result.envelopes[0]
    assert envelope.error is not None
    assert envelope.error.code == "deferred_tool_schema_probe"
    assert "required" in (envelope.error.message or "").lower()
    assert "target" in (envelope.error.message or "")


@pytest.mark.asyncio
async def test_deferred_with_required_dispatches_normally() -> None:
    registry, calls = _registry(deferred=True)
    result = await _run_call(registry, {"target": "lab"})
    assert calls["n"] == 1  # required arg present → real dispatch
    assert result.envelopes[0].error is None


@pytest.mark.asyncio
async def test_non_deferred_tool_is_not_probed() -> None:
    # A normal (non-deferred) tool the model saw the schema for is dispatched as-is;
    # arg validation stays the tool's own concern.
    registry, calls = _registry(deferred=False)
    result = await _run_call(registry, {})
    assert calls["n"] == 1
    assert result.envelopes[0].error is None
