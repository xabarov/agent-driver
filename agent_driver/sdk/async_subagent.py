"""In-process background subagents (D6): start / check / cancel by task id.

``run_subagent`` is blocking — the caller awaits the child to completion. For
fan-out where the parent wants to kick off work and keep going, this wraps it in
an ``asyncio.Task`` and hands back a pollable, cancellable handle keyed by a
task id. This is the lightweight in-process variant (no remote Agent Protocol):
the children run in the same event loop as the parent.

Cancellation flips a :class:`RunAbortHandle` (cascades into the child at its next
step boundary) and cancels the task, so a long child stops promptly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from uuid import uuid4

from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.tool_gate import ToolGate
from agent_driver.sdk.agent import Agent
from agent_driver.sdk.subagent import SubagentResult, SubagentSpec, run_subagent

_PENDING = "pending"
_RUNNING = "running"
_DONE = "done"
_FAILED = "failed"
_CANCELLED = "cancelled"


@dataclass(slots=True)
class BackgroundSubagent:
    """A handle to one in-flight (or finished) background child run."""

    task_id: str
    agent_type: str
    _task: "asyncio.Task[SubagentResult]"
    _abort: RunAbortHandle

    def status(self) -> str:
        """One of pending / running / done / failed / cancelled."""
        if not self._task.done():
            # ``pending`` until the event loop has actually started the coro.
            return _RUNNING if self._started() else _PENDING
        if self._task.cancelled():
            return _CANCELLED
        return _FAILED if self._task.exception() is not None else _DONE

    def done(self) -> bool:
        """True once the task has finished (any terminal status)."""
        return self._task.done()

    def result_if_ready(self) -> SubagentResult | None:
        """Return the result if finished successfully, else ``None``."""
        if self._task.done() and not self._task.cancelled():
            if self._task.exception() is None:
                return self._task.result()
        return None

    async def result(self) -> SubagentResult:
        """Await and return the child's result (raises if it failed)."""
        return await self._task

    def cancel(self) -> None:
        """Abort the child (cascades) and cancel the background task."""
        self._abort.abort(reason="cancelled_by_parent")
        if not self._task.done():
            self._task.cancel()

    def _started(self) -> bool:
        # asyncio.Task has no public "started" flag; treat a not-done task as
        # running once it exists. Kept as a hook for clarity.
        return True


@dataclass(slots=True)
class AsyncSubagentManager:
    """Track background child runs spawned from one parent agent."""

    parent: Agent
    _tasks: dict[str, BackgroundSubagent] = field(default_factory=dict)

    def start(
        self,
        spec: SubagentSpec,
        *,
        parent_run_id: str | None = None,
        tool_gate: ToolGate | None = None,
    ) -> BackgroundSubagent:
        """Spawn ``spec`` as a background task; return its handle."""
        task_id = f"async_{uuid4().hex[:12]}"
        abort = RunAbortHandle()
        task: asyncio.Task[SubagentResult] = asyncio.create_task(
            run_subagent(
                self.parent,
                spec,
                parent_run_id=parent_run_id,
                parent_abort_handle=abort,
                tool_gate=tool_gate,
            ),
            name=task_id,
        )
        handle = BackgroundSubagent(
            task_id=task_id, agent_type=spec.agent_type, _task=task, _abort=abort
        )
        self._tasks[task_id] = handle
        return handle

    def get(self, task_id: str) -> BackgroundSubagent | None:
        """Look up a handle by task id."""
        return self._tasks.get(task_id)

    def list_tasks(self) -> list[BackgroundSubagent]:
        """All tracked handles (any status)."""
        return list(self._tasks.values())

    def cancel(self, task_id: str) -> bool:
        """Cancel one task by id; return whether it was found."""
        handle = self._tasks.get(task_id)
        if handle is None:
            return False
        handle.cancel()
        return True

    async def gather(self) -> dict[str, SubagentResult | None]:
        """Await all tracked tasks; map task id → result (None if cancelled)."""
        results: dict[str, SubagentResult | None] = {}
        for task_id, handle in self._tasks.items():
            try:
                results[task_id] = await handle.result()
            except asyncio.CancelledError:
                results[task_id] = None
        return results


__all__ = ["AsyncSubagentManager", "BackgroundSubagent"]
