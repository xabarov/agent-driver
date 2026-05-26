"""Phase 11 H12 — tests for concurrent tool-call partitioning.

Pins the contract: adjacent concurrency-safe calls group into a single
parallel batch; any unsafe call becomes a serial unit; original order
is preserved across units AND inside batches.
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import (
    AgentProfile,
    ApprovalMode,
    SideEffectClass,
    ToolRisk,
)
from agent_driver.contracts.tools import ToolCall, ToolManifest
from agent_driver.tools.executor.partition import (
    ParallelBatch,
    SerialCall,
    is_call_concurrency_safe,
    partition_concurrent_calls,
)


def _call(name: str, **args) -> ToolCall:
    return ToolCall(tool_name=name, args=args)


def _safe_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"safe-read {name}",
        risk=ToolRisk.LOW,
        side_effect=SideEffectClass.READ_ONLY,
        approval_mode=ApprovalMode.NEVER,
        idempotent=True,
    )


def _write_manifest(name: str) -> ToolManifest:
    return ToolManifest(
        name=name,
        description=f"write {name}",
        risk=ToolRisk.MEDIUM,
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        approval_mode=ApprovalMode.NEVER,
        idempotent=False,
    )


def test_empty_calls_yields_empty_units():
    units = partition_concurrent_calls([], is_safe=lambda _c: True)
    assert units == []


def test_all_safe_groups_into_one_parallel_batch():
    calls = [_call("a"), _call("b"), _call("c")]
    units = partition_concurrent_calls(calls, is_safe=lambda _c: True)
    assert len(units) == 1
    assert isinstance(units[0], ParallelBatch)
    assert units[0].items == tuple(calls)


def test_all_unsafe_yields_only_serial_units():
    calls = [_call("a"), _call("b"), _call("c")]
    units = partition_concurrent_calls(calls, is_safe=lambda _c: False)
    assert len(units) == 3
    assert all(isinstance(u, SerialCall) for u in units)
    assert [u.item.tool_name for u in units] == ["a", "b", "c"]


def test_mixed_groups_preserve_order_and_partition_correctly():
    # Read Read Write Read Read Read Write Read
    # → batch(R, R), serial(W), batch(R, R, R), serial(W), batch(R)
    calls = [
        _call("r1"),
        _call("r2"),
        _call("w1"),
        _call("r3"),
        _call("r4"),
        _call("r5"),
        _call("w2"),
        _call("r6"),
    ]
    is_write = {"w1", "w2"}
    units = partition_concurrent_calls(
        calls,
        is_safe=lambda c: c.tool_name not in is_write,
    )
    assert len(units) == 5
    assert isinstance(units[0], ParallelBatch)
    assert [c.tool_name for c in units[0].items] == ["r1", "r2"]
    assert isinstance(units[1], SerialCall)
    assert units[1].item.tool_name == "w1"
    assert isinstance(units[2], ParallelBatch)
    assert [c.tool_name for c in units[2].items] == ["r3", "r4", "r5"]
    assert isinstance(units[3], SerialCall)
    assert units[3].item.tool_name == "w2"
    assert isinstance(units[4], ParallelBatch)
    assert [c.tool_name for c in units[4].items] == ["r6"]


def test_batch_limit_splits_long_safe_runs():
    """A run of 7 safe calls with batch_limit=3 → 3 batches (3, 3, 1)."""
    calls = [_call(f"r{i}") for i in range(7)]
    units = partition_concurrent_calls(
        calls,
        is_safe=lambda _c: True,
        batch_limit=3,
    )
    assert len(units) == 3
    assert all(isinstance(u, ParallelBatch) for u in units)
    assert len(units[0].items) == 3
    assert len(units[1].items) == 3
    assert len(units[2].items) == 1
    # Order across batches still correct.
    flat = [c.tool_name for u in units for c in u.items]
    assert flat == [c.tool_name for c in calls]


def test_batch_limit_zero_raises():
    with pytest.raises(ValueError, match="batch_limit must be positive"):
        partition_concurrent_calls(
            [_call("a")],
            is_safe=lambda _c: True,
            batch_limit=0,
        )


def test_manifest_is_concurrency_safe_default_derivation():
    """Default ToolManifest (idempotent=True, side_effect=NONE) is safe."""
    m = ToolManifest(name="lookup", description="lookup")
    assert m.is_concurrency_safe() is True


def test_manifest_write_side_effect_not_safe_by_default():
    m = _write_manifest("file_write")
    assert m.is_concurrency_safe() is False


def test_manifest_external_action_not_safe_by_default():
    m = ToolManifest(
        name="http_post",
        description="external",
        side_effect=SideEffectClass.EXTERNAL_ACTION,
        idempotent=False,
    )
    assert m.is_concurrency_safe() is False


def test_manifest_explicit_concurrency_safe_overrides_default():
    """Operator marks an idempotent but rate-limited tool as serial."""
    m = ToolManifest(
        name="external_api",
        description="rate-limited read",
        side_effect=SideEffectClass.READ_ONLY,
        idempotent=True,
        concurrency_safe=False,
    )
    assert m.is_concurrency_safe() is False


def test_manifest_explicit_safe_overrides_derivation_to_true():
    """Operator opts a write into parallel execution (e.g. distinct files)."""
    m = ToolManifest(
        name="distinct_file_write",
        description="writes own file per call",
        side_effect=SideEffectClass.REVERSIBLE_WRITE,
        idempotent=False,
        concurrency_safe=True,
    )
    assert m.is_concurrency_safe() is True


def test_is_call_concurrency_safe_uses_registered_manifest():
    manifests = {
        "read_tool": _safe_manifest("read_tool"),
        "write_tool": _write_manifest("write_tool"),
    }

    def lookup(name: str):
        return manifests.get(name)

    assert is_call_concurrency_safe(_call("read_tool"), manifest_lookup=lookup) is True
    assert is_call_concurrency_safe(_call("write_tool"), manifest_lookup=lookup) is False


def test_is_call_concurrency_safe_unregistered_returns_false():
    """Unknown tool name routes to serial slot — preserves error path."""

    def lookup(_name: str):
        return None

    assert is_call_concurrency_safe(_call("ghost_tool"), manifest_lookup=lookup) is False


def test_is_call_concurrency_safe_swallows_predicate_errors():
    """Custom manifest whose is_concurrency_safe raises → defaults to False."""

    class BadManifest:
        def is_concurrency_safe(self):
            raise RuntimeError("explode")

    def lookup(_name: str):
        return BadManifest()

    assert is_call_concurrency_safe(_call("buggy"), manifest_lookup=lookup) is False
