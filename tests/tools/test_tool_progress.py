"""Phase 11 H16 — tests for tool progress streaming.

Pins the contract: ``report_tool_progress(...)`` inside a handler
populates ``result.progress_events`` with the originating call_index
and tool_name; when no reporter is wired (default), the call is a
silent no-op. Progress entries appear in emission order (interleaved
across parallel calls but per-call sequence preserved).
"""

from __future__ import annotations

import asyncio

import pytest

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
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.tools import GovernedToolExecutor, ToolRegistry
from agent_driver.tools.context import (
    ToolProgress,
    report_tool_progress,
    tool_progress_scope,
)
from tests.runtime.conftest import llm_request_with_planned_calls


def _build_run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


def test_report_tool_progress_without_scope_is_silent():
    """Default ContextVar=None → no reporter wired → call is a no-op."""
    # Must not raise; we just call it.
    report_tool_progress(kind="x", message="hello")
    report_tool_progress(kind="x", message="world", completion_ratio=0.5)


def test_tool_progress_scope_invokes_reporter():
    captured: list[ToolProgress] = []

    def reporter(p: ToolProgress) -> None:
        captured.append(p)

    with tool_progress_scope(reporter):
        report_tool_progress(kind="scan", message="50/100", completion_ratio=0.5)
        report_tool_progress(kind="scan", message="100/100", completion_ratio=1.0)

    assert len(captured) == 2
    assert captured[0].kind == "scan"
    assert captured[0].message == "50/100"
    assert captured[0].completion_ratio == 0.5
    assert captured[1].completion_ratio == 1.0


def test_tool_progress_scope_resets_after_exit():
    captured: list[ToolProgress] = []
    with tool_progress_scope(lambda p: captured.append(p)):
        report_tool_progress(kind="a", message="inside")
    # After scope exits, calls become silent no-op again.
    report_tool_progress(kind="b", message="outside")
    assert len(captured) == 1
    assert captured[0].message == "inside"


def test_progress_reporter_swallows_errors():
    """Reporter raising must not crash the handler call."""
    def boom(_p: ToolProgress) -> None:
        raise RuntimeError("sink failed")

    with tool_progress_scope(boom):
        # No exception propagates.
        report_tool_progress(kind="ok", message="value")


@pytest.mark.asyncio
async def test_executor_records_progress_into_result() -> None:
    """End-to-end: handler emits progress; executor result captures them."""
    registry = ToolRegistry()

    async def _slow_scan(_args):
        report_tool_progress(kind="scan", message="phase 1/3", completion_ratio=0.33)
        await asyncio.sleep(0)
        report_tool_progress(kind="scan", message="phase 2/3", completion_ratio=0.66)
        await asyncio.sleep(0)
        report_tool_progress(kind="scan", message="phase 3/3", completion_ratio=1.0)
        return {"summary": "scan done"}

    registry.register(
        ToolManifest(
            name="long_scan",
            description="long scan with progress",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            output_char_budget=2000,
        ),
        _slow_scan,
    )

    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="long_scan", args={})]
        )
    )
    result = await executor.execute(_build_run_input("run_progress"), response)

    assert len(result.progress_events) == 3
    for entry in result.progress_events:
        assert entry.tool_name == "long_scan"
        assert entry.call_index == 1
    assert [e.progress.message for e in result.progress_events] == [
        "phase 1/3",
        "phase 2/3",
        "phase 3/3",
    ]
    assert result.progress_events[0].progress.completion_ratio == 0.33
    assert result.progress_events[-1].progress.completion_ratio == 1.0


@pytest.mark.asyncio
async def test_progress_handler_not_called_yields_no_events() -> None:
    """Tools that don't call report_tool_progress add nothing."""
    registry = ToolRegistry()

    async def _quiet(_args):
        return {"summary": "ok"}

    registry.register(
        ToolManifest(
            name="quiet_tool",
            description="silent",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
        ),
        _quiet,
    )
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name="quiet_tool", args={})]
        )
    )
    result = await executor.execute(_build_run_input("run_quiet"), response)
    assert result.progress_events == []


@pytest.mark.asyncio
async def test_parallel_progress_events_correlated_to_call_index() -> None:
    """In parallel batch, each handler's progress carries its own call_index."""
    registry = ToolRegistry()

    async def _make_handler(name: str):
        async def _handler(_args):
            report_tool_progress(kind="start", message=f"{name}:begin")
            await asyncio.sleep(0)
            report_tool_progress(kind="end", message=f"{name}:done")
            return {"summary": name}

        return _handler

    for name in ("alpha", "beta", "gamma"):
        registry.register(
            ToolManifest(
                name=name,
                description=f"reader {name}",
                risk=ToolRisk.LOW,
                side_effect=SideEffectClass.READ_ONLY,
                approval_mode=ApprovalMode.NEVER,
                idempotent=True,
            ),
            await _make_handler(name),
        )

    executor = GovernedToolExecutor(registry=registry, concurrency_limit=4)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="alpha", args={}),
                ToolCall(tool_name="beta", args={}),
                ToolCall(tool_name="gamma", args={}),
            ]
        )
    )
    result = await executor.execute(_build_run_input("run_par_progress"), response)

    # Six total events (3 tools × 2 each). Each tool's events stay
    # paired with the correct call_index even though merge ordering
    # depends on completion.
    assert len(result.progress_events) == 6
    by_index: dict[int, list[str]] = {}
    for entry in result.progress_events:
        by_index.setdefault(entry.call_index, []).append(entry.progress.message)
    # Indices 1, 2, 3 (one per planned call).
    assert set(by_index.keys()) == {1, 2, 3}
    # Each index has both its messages in original begin→done order
    # (per-task chronology preserved).
    assert by_index[1] == ["alpha:begin", "alpha:done"]
    assert by_index[2] == ["beta:begin", "beta:done"]
    assert by_index[3] == ["gamma:begin", "gamma:done"]
