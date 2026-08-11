"""Concurrent subagent fan-out with a join policy (coordination C1).

The SDK could spawn one child (`run_subagent`) or background handles
(`AsyncSubagentManager`), but had no single "run these N specs concurrently, capped,
and join them under a policy" primitive — so a consumer (excel-ai) re-implemented
parallel fan-out with its own `asyncio.gather` + semaphore, and the runtime's formal
join policies (`SubagentJoinPolicy`) were reachable only from the model-planner path.

`run_subagent_group` closes that: it runs a list of `SubagentSpec` concurrently under
a concurrency cap and *executes* the shared `SubagentJoinPolicy` vocabulary with
asyncio — `WAIT_ALL` (await all), `WAIT_ANY` (return on the first success, cancel the
rest), `K_OF_N` (return when k succeed), `RACE` (first to finish wins), and
`BEST_EFFORT_UNTIL_DEADLINE` (take whatever finished by a deadline). A failed child
never aborts the group; results and errors come back aligned to the input specs.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from agent_driver.contracts.enums import RunStatus, SubagentJoinPolicy
from agent_driver.llm.backoff import abort_aware_sleep, jittered_delay
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec, run_subagent

# Default retry predicate: retry a child that raised, or that ended non-COMPLETED.
def _default_retry_on(
    result: SubagentResult | None, error: BaseException | None
) -> bool:
    return error is not None or (
        result is not None and result.status != RunStatus.COMPLETED
    )


@dataclass(frozen=True, slots=True)
class SubagentGroupResult:
    """Outcome of a fan-out group, aligned to the input specs.

    ``results[i]`` is child *i*'s :class:`SubagentResult` (any terminal status) or
    ``None`` if it errored / was cancelled by an early-exit policy; ``errors[i]`` is
    the exception child *i* raised, or ``None``. ``satisfied`` is whether the join
    policy's success goal was met.
    """

    results: tuple[SubagentResult | None, ...]
    errors: tuple[BaseException | None, ...]
    join_policy: SubagentJoinPolicy
    satisfied: bool

    @property
    def completed(self) -> list[SubagentResult]:
        """Successful (``COMPLETED``) child results, in input-spec order."""
        return [
            item
            for item in self.results
            if item is not None and item.status == RunStatus.COMPLETED
        ]

    @property
    def succeeded(self) -> int:
        """How many children completed successfully."""
        return len(self.completed)

    @property
    def failed(self) -> int:
        """How many children raised or ended non-``COMPLETED``."""
        return sum(
            1
            for result, error in zip(self.results, self.errors)
            if error is not None
            or (result is not None and result.status != RunStatus.COMPLETED)
        )


def _is_success(result: SubagentResult | None) -> bool:
    return result is not None and result.status == RunStatus.COMPLETED


async def run_subagent_group(
    parent: Agent,
    specs: Iterable[SubagentSpec],
    *,
    join_policy: SubagentJoinPolicy = SubagentJoinPolicy.WAIT_ALL,
    concurrency: int | None = None,
    k: int | None = None,
    deadline_seconds: float | None = None,
    retries: int = 0,
    retry_on: Callable[[SubagentResult | None, BaseException | None], bool] | None = None,
    retry_backoff_seconds: float = 0.0,
    parent_run_id: str | None = None,
    parent_abort_handle: RunAbortHandle | None = None,
    tool_gate: ToolGate | None = None,
) -> SubagentGroupResult:
    """Run ``specs`` concurrently and join under ``join_policy``.

    ``concurrency`` caps how many children run at once (default: all). ``k`` is
    required for ``K_OF_N``; ``deadline_seconds`` for ``BEST_EFFORT_UNTIL_DEADLINE``.
    Early-exit policies (``WAIT_ANY`` / ``K_OF_N`` / ``RACE``) cancel the still-running
    children once satisfied. ``parent_run_id`` / ``parent_abort_handle`` / ``tool_gate``
    forward to every ``run_subagent``. Never raises for a child failure — inspect
    ``.errors`` / ``.completed``. ``MANUAL_REVIEW`` behaves like ``WAIT_ALL`` (the
    review step is the caller's).

    ``retries`` re-runs a child that fails (per ``retry_on``, default: it raised or
    ended non-``COMPLETED``), up to ``retries`` times, with a ``retry_backoff_seconds``
    exponential+jittered wait **outside** the concurrency slot — a backing-off child
    frees its slot for the next queued one. The backoff is abort-aware.
    """
    spec_list = list(specs)
    count = len(spec_list)
    if join_policy == SubagentJoinPolicy.K_OF_N and (k is None or k < 1):
        raise ValueError("K_OF_N join policy requires k >= 1")
    results: list[SubagentResult | None] = [None] * count
    errors: list[BaseException | None] = [None] * count
    if count == 0:
        return SubagentGroupResult((), (), join_policy, satisfied=True)

    sem = asyncio.Semaphore(concurrency) if concurrency and concurrency > 0 else None
    should_retry = retry_on or _default_retry_on
    abort_check = (
        (lambda: bool(getattr(parent_abort_handle, "is_aborted", False)))
        if parent_abort_handle is not None
        else None
    )

    async def _attempt(index: int) -> tuple[SubagentResult | None, BaseException | None]:
        try:
            if sem is not None:
                async with sem:
                    return (
                        await run_subagent(
                            parent,
                            spec_list[index],
                            parent_run_id=parent_run_id,
                            parent_abort_handle=parent_abort_handle,
                            tool_gate=tool_gate,
                        ),
                        None,
                    )
            return (
                await run_subagent(
                    parent,
                    spec_list[index],
                    parent_run_id=parent_run_id,
                    parent_abort_handle=parent_abort_handle,
                    tool_gate=tool_gate,
                ),
                None,
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:  # noqa: BLE001 - one child's failure abstains
            return None, exc

    async def _one(index: int) -> tuple[int, SubagentResult | None, BaseException | None]:
        attempt = 0
        while True:
            child, error = await _attempt(index)
            if attempt >= retries or not should_retry(child, error):
                return index, child, error
            attempt += 1
            if retry_backoff_seconds > 0:
                # Backoff OUTSIDE the semaphore so it doesn't hold a concurrency slot.
                await abort_aware_sleep(
                    jittered_delay(retry_backoff_seconds * (2 ** (attempt - 1))),
                    abort_check=abort_check,
                )

    tasks = {asyncio.create_task(_one(i)) for i in range(count)}
    target = k or 1 if join_policy == SubagentJoinPolicy.K_OF_N else 1

    def _record(done: set[asyncio.Task]) -> None:
        for task in done:
            index, child, error = task.result()
            results[index] = child
            errors[index] = error

    pending = set(tasks)
    try:
        if join_policy == SubagentJoinPolicy.BEST_EFFORT_UNTIL_DEADLINE:
            done, pending = await asyncio.wait(pending, timeout=deadline_seconds)
            _record(done)
        else:
            while pending:
                done, pending = await asyncio.wait(
                    pending, return_when=asyncio.FIRST_COMPLETED
                )
                _record(done)
                if join_policy in (
                    SubagentJoinPolicy.WAIT_ALL,
                    SubagentJoinPolicy.MANUAL_REVIEW,
                ):
                    continue  # drain everything
                if join_policy == SubagentJoinPolicy.RACE:
                    break  # first to finish (any status) wins
                if sum(1 for item in results if _is_success(item)) >= target:
                    break  # WAIT_ANY / K_OF_N satisfied
    finally:
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)

    successes = sum(1 for item in results if _is_success(item))
    if join_policy in (SubagentJoinPolicy.WAIT_ALL, SubagentJoinPolicy.MANUAL_REVIEW):
        satisfied = successes == count
    elif join_policy == SubagentJoinPolicy.K_OF_N:
        satisfied = successes >= target
    elif join_policy == SubagentJoinPolicy.RACE:
        satisfied = any(item is not None for item in results)
    else:  # WAIT_ANY, BEST_EFFORT_UNTIL_DEADLINE
        satisfied = successes >= 1

    return SubagentGroupResult(
        results=tuple(results),
        errors=tuple(errors),
        join_policy=join_policy,
        satisfied=satisfied,
    )


__all__ = ["SubagentGroupResult", "run_subagent_group"]
