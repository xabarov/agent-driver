"""Phase 11 H12 — end-to-end tests for parallel tool execution.

Pins the contract: adjacent concurrency-safe tools complete in roughly
``max(per_tool_latency)`` wall-clock time (parallel), while writes serialize.
Result envelopes/traces still appear in LLM-emit order regardless of
which task completed first.
"""

from __future__ import annotations

import asyncio
import time

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
from tests.runtime.conftest import llm_request_with_planned_calls


def _register_slow_read(registry: ToolRegistry, name: str, delay_s: float) -> None:
    async def _handler(args):  # noqa: ARG001
        await asyncio.sleep(delay_s)
        return {"summary": f"done:{name}"}

    registry.register(
        ToolManifest(
            name=name,
            description=f"slow read {name}",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            output_char_budget=2000,
        ),
        _handler,
    )


def _register_slow_write(registry: ToolRegistry, name: str, delay_s: float) -> None:
    async def _handler(args):  # noqa: ARG001
        await asyncio.sleep(delay_s)
        return {"summary": f"done:{name}"}

    registry.register(
        ToolManifest(
            name=name,
            description=f"slow write {name}",
            risk=ToolRisk.MEDIUM,
            side_effect=SideEffectClass.REVERSIBLE_WRITE,
            approval_mode=ApprovalMode.NEVER,
            idempotent=False,
            output_char_budget=2000,
        ),
        _handler,
    )


def _build_run_input(run_id: str) -> AgentRunInput:
    return AgentRunInput(
        input="hello",
        run_id=run_id,
        agent_id="agent",
        graph_preset="single_react",
        tool_policy=ToolPolicyInput(mode=ToolPolicyMode.ALLOW_TOOLS),
    )


@pytest.mark.asyncio
async def test_three_read_tools_run_in_parallel() -> None:
    """Three read-only calls × 80ms each → parallel ≈ 80ms, not 240ms."""
    registry = ToolRegistry()
    for name in ("r1", "r2", "r3"):
        _register_slow_read(registry, name, delay_s=0.08)
    executor = GovernedToolExecutor(registry=registry, concurrency_limit=4)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="r1", args={}),
                ToolCall(tool_name="r2", args={}),
                ToolCall(tool_name="r3", args={}),
            ]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(_build_run_input("run_parallel_reads"), response)
    elapsed = time.perf_counter() - started

    # Sequential would take ≥ 0.24s. Allow generous slack for slow CI: < 0.20s.
    assert elapsed < 0.20, f"parallel reads took {elapsed:.3f}s (expected ≪ 0.24s)"
    assert len(result.traces) == 3
    # Order preserved in result.
    assert [trace.tool_name for trace in result.traces] == ["r1", "r2", "r3"]


@pytest.mark.asyncio
async def test_writes_serialize_after_parallel_reads() -> None:
    """Mixed sequence R R W R R: reads batch, write isolates, last two reads batch.

    Total wall time ≈ delay(R) + delay(W) + delay(R), not 5×delay.
    """
    registry = ToolRegistry()
    for r in ("r1", "r2", "r3", "r4"):
        _register_slow_read(registry, r, delay_s=0.05)
    _register_slow_write(registry, "w1", delay_s=0.05)

    executor = GovernedToolExecutor(registry=registry, concurrency_limit=8)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="r1", args={}),
                ToolCall(tool_name="r2", args={}),
                ToolCall(tool_name="w1", args={}),
                ToolCall(tool_name="r3", args={}),
                ToolCall(tool_name="r4", args={}),
            ]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(_build_run_input("run_mixed"), response)
    elapsed = time.perf_counter() - started

    # Expected: parallel(r1, r2)→serial(w1)→parallel(r3, r4) ≈ 3×0.05 = 0.15s.
    # Sequential would be 5×0.05 = 0.25s.
    assert elapsed < 0.20, f"mixed run took {elapsed:.3f}s (expected ≈ 0.15s)"
    assert [t.tool_name for t in result.traces] == ["r1", "r2", "w1", "r3", "r4"]


@pytest.mark.asyncio
async def test_concurrency_limit_caps_parallel_coroutines() -> None:
    """concurrency_limit=2 with 4 reads → 2 batches of 2, total ≈ 2×delay."""
    registry = ToolRegistry()
    for r in ("a", "b", "c", "d"):
        _register_slow_read(registry, r, delay_s=0.06)
    executor = GovernedToolExecutor(registry=registry, concurrency_limit=2)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name=n, args={}) for n in ("a", "b", "c", "d")]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(_build_run_input("run_capped"), response)
    elapsed = time.perf_counter() - started

    # All 4 calls form one parallel batch (partitioner doesn't split — it
    # cares about safety only). The SEMAPHORE inside the batch caps
    # concurrency to 2, so we see 2 waves of 2 = ~0.12s.
    assert elapsed >= 0.10, f"capped run took {elapsed:.3f}s (expected ≥ 0.10s)"
    assert elapsed < 0.18, f"capped run took {elapsed:.3f}s (expected ≪ 0.24s sequential)"
    assert [t.tool_name for t in result.traces] == ["a", "b", "c", "d"]


@pytest.mark.asyncio
async def test_all_writes_serialize() -> None:
    """All non-safe → no partition collapses → sequential timing."""
    registry = ToolRegistry()
    for w in ("w1", "w2", "w3"):
        _register_slow_write(registry, w, delay_s=0.04)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[ToolCall(tool_name=n, args={}) for n in ("w1", "w2", "w3")]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(_build_run_input("run_writes"), response)
    elapsed = time.perf_counter() - started

    # ≈ 3 × 0.04 = 0.12s. Parallel would be ≈ 0.04s — we want the higher.
    assert elapsed >= 0.10, f"writes ran in {elapsed:.3f}s (expected serial ≥ 0.10s)"
    assert [t.tool_name for t in result.traces] == ["w1", "w2", "w3"]


@pytest.mark.asyncio
async def test_parallel_result_order_independent_of_completion_order() -> None:
    """Even when call 1 takes longer than calls 2/3, traces appear in
    [1, 2, 3] not in completion order [2, 3, 1]."""
    registry = ToolRegistry()
    _register_slow_read(registry, "slow", delay_s=0.10)
    _register_slow_read(registry, "fast_a", delay_s=0.02)
    _register_slow_read(registry, "fast_b", delay_s=0.02)
    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="slow", args={}),
                ToolCall(tool_name="fast_a", args={}),
                ToolCall(tool_name="fast_b", args={}),
            ]
        )
    )
    result = await executor.execute(_build_run_input("run_order"), response)
    # Original LLM order preserved despite completion order.
    assert [t.tool_name for t in result.traces] == ["slow", "fast_a", "fast_b"]


@pytest.mark.asyncio
async def test_concurrency_safe_explicit_false_keeps_tool_serial() -> None:
    """Rate-limited read marked concurrency_safe=False runs as its own slot."""
    registry = ToolRegistry()
    _register_slow_read(registry, "regular_read", delay_s=0.05)

    async def _rate_limited(args):  # noqa: ARG001
        await asyncio.sleep(0.05)
        return {"summary": "rate-limited done"}

    registry.register(
        ToolManifest(
            name="rate_limited_read",
            description="rate-limited read",
            risk=ToolRisk.LOW,
            side_effect=SideEffectClass.READ_ONLY,
            approval_mode=ApprovalMode.NEVER,
            idempotent=True,
            concurrency_safe=False,
        ),
        _rate_limited,
    )

    executor = GovernedToolExecutor(registry=registry)
    provider = FakeProvider(response_text="ok")
    response = await provider.complete(
        llm_request_with_planned_calls(
            planned=[
                ToolCall(tool_name="regular_read", args={}),
                ToolCall(tool_name="rate_limited_read", args={}),
                ToolCall(tool_name="regular_read", args={}),
            ]
        )
    )
    started = time.perf_counter()
    result = await executor.execute(_build_run_input("run_rate_lim"), response)
    elapsed = time.perf_counter() - started

    # rate_limited splits the run: regular_read|rate_limited|regular_read.
    # Each non-overlapping → ~3×0.05 = 0.15s (with some slack).
    assert elapsed >= 0.13
    assert [t.tool_name for t in result.traces] == [
        "regular_read",
        "rate_limited_read",
        "regular_read",
    ]
