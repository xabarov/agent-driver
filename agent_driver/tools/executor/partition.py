"""Phase 11 H12 — partition tool calls into parallel batches and serial calls.

Mirrors openclaude ``src/services/tools/toolOrchestration.ts:19-82``: a
sequence of planned tool calls becomes a list of execution units where
adjacent concurrency-safe calls are grouped into a single parallel
batch, and any non-safe call is its own serial unit.

The algorithm preserves the original call order — both within a parallel
batch (so result indices stay deterministic for the LLM) and across the
sequence (so a write that follows three reads still executes after them).
This matches the contract a single-threaded executor would honor; the
only behaviour change is that adjacent reads run concurrently instead of
sequentially.

Example::

    A(read) B(read) C(write) D(read) E(read) F(read)

    → [parallel(A, B), serial(C), parallel(D, E, F)]

Concurrency cap is enforced at execution time via ``asyncio.Semaphore``;
the partitioner just groups by safety predicate.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar

from agent_driver.contracts.tools import ToolCall

T = TypeVar("T")


@dataclass(frozen=True)
class ParallelBatch(Generic[T]):
    """One or more concurrency-safe calls to run in parallel.

    ``items`` retains the original order from the planned-calls list so
    that result indices remain stable.
    """

    items: tuple[T, ...]


@dataclass(frozen=True)
class SerialCall(Generic[T]):
    """A single non-concurrency-safe call that must run in isolation."""

    item: T


ExecutionUnit = ParallelBatch[T] | SerialCall[T]


def partition_concurrent_calls(
    calls: Sequence[T],
    *,
    is_safe: Callable[[T], bool],
    batch_limit: int | None = None,
) -> list[ExecutionUnit[T]]:
    """Group adjacent concurrency-safe calls; emit non-safe calls solo.

    Args:
        calls: planned tool calls in original LLM-emit order.
        is_safe: predicate deciding whether a call is parallel-safe.
            Typically wraps ``ToolManifest.is_concurrency_safe()`` plus
            any per-input refinements.
        batch_limit: optional cap on items per parallel batch. When a
            run of safe calls exceeds the cap, the partitioner splits
            them into multiple batches (each up to ``batch_limit``).
            ``None`` means unbounded batch size — the executor still
            applies a semaphore at run time.

    Returns:
        Ordered list of ``ParallelBatch`` and ``SerialCall`` units. The
        union of all items equals ``calls`` in the same order.
    """
    if batch_limit is not None and batch_limit <= 0:
        raise ValueError("batch_limit must be positive when set")
    units: list[ExecutionUnit[T]] = []
    pending: list[T] = []

    def flush_pending() -> None:
        if not pending:
            return
        if batch_limit is None:
            units.append(ParallelBatch(items=tuple(pending)))
        else:
            for start in range(0, len(pending), batch_limit):
                chunk = pending[start : start + batch_limit]
                units.append(ParallelBatch(items=tuple(chunk)))
        pending.clear()

    for call in calls:
        if is_safe(call):
            pending.append(call)
        else:
            flush_pending()
            units.append(SerialCall(item=call))
    flush_pending()
    return units


def is_call_concurrency_safe(
    call: ToolCall,
    *,
    manifest_lookup: Callable[[str], object | None],
) -> bool:
    """Default safety predicate: consult the registered manifest.

    Args:
        call: planned tool call.
        manifest_lookup: callable that returns the registered
            ``ToolManifest`` for a tool name (or ``None`` when the tool is
            not registered — those calls are treated as NOT safe so the
            executor's existing missing-tool error path runs serially).

    Returns:
        True only when the registered manifest's ``is_concurrency_safe()``
        returns True. Unregistered tools default to False.
    """
    manifest = manifest_lookup(call.tool_name)
    if manifest is None:
        return False
    is_safe = getattr(manifest, "is_concurrency_safe", None)
    if is_safe is None:
        return False
    try:
        return bool(is_safe())
    except Exception:
        # Defensive — if a custom manifest's resolver raises, fall back
        # to serial execution rather than crashing the partitioner.
        return False


__all__ = [
    "ExecutionUnit",
    "ParallelBatch",
    "SerialCall",
    "is_call_concurrency_safe",
    "partition_concurrent_calls",
]
