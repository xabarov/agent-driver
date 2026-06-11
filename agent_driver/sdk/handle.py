"""Run handle and stream helpers for SDK callers."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass

from agent_driver.contracts.checkpoints import CheckpointRef
from agent_driver.contracts.enums import RuntimeEventType
from agent_driver.contracts.runtime import AgentRunOutput
from agent_driver.contracts.stream import RunStreamEvent
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.runtime.storage import CheckpointStore, RuntimeEventLog
from agent_driver.runtime.stream import project_runtime_events


@dataclass(slots=True)
class RunHandle:
    """Handle for a background SDK run."""

    run_id: str
    _task: asyncio.Task[AgentRunOutput]
    _abort_handle: RunAbortHandle
    _event_log: RuntimeEventLog
    _checkpoint_store: CheckpointStore

    def events(self, *, after_seq: int | None = None) -> list[RunStreamEvent]:
        """Return persisted stream events for this run."""
        return project_runtime_events(
            self._event_log.list_for_run(self.run_id, after_seq=after_seq)
        )

    async def final(self) -> AgentRunOutput:
        """Await and return the final run output."""
        return await self._task

    def done(self) -> bool:
        """Return whether the background run task has finished."""
        return self._task.done()

    def abort(self, reason: str = "sdk_abort") -> None:
        """Request cancellation at the next runtime step boundary."""
        self._abort_handle.abort(reason=reason)

    async def close(self) -> None:
        """Cancel the local task if the stream consumer stops early."""
        if self._task.done():
            return
        self._task.cancel()
        with suppress(asyncio.CancelledError):
            await self._task

    def checkpoint(self) -> CheckpointRef | None:
        """Return the latest checkpoint reference for this run, if present."""
        row = self._checkpoint_store.latest(self.run_id)
        return row.ref if row is not None else None


class RunStream:
    """Polling stream helper over the durable runtime event log."""

    def __init__(
        self,
        handle: RunHandle,
        *,
        poll_interval_seconds: float = 0.02,
    ) -> None:
        self._handle = handle
        self._poll_interval_seconds = max(0.01, poll_interval_seconds)
        self.cursor = 0

    @property
    def run_id(self) -> str:
        """Run identifier for this stream."""
        return self._handle.run_id

    async def events(self) -> AsyncIterator[RunStreamEvent]:
        """Yield stream events until the run finishes."""
        try:
            while True:
                emitted = False
                for event in self._handle.events(after_seq=self.cursor):
                    self.cursor = event.seq
                    emitted = True
                    yield event
                if self._handle.done():
                    break
                if not emitted:
                    await asyncio.sleep(self._poll_interval_seconds)
            await self._handle.final()
            for event in self._handle.events(after_seq=self.cursor):
                self.cursor = event.seq
                yield event
        finally:
            await self._handle.close()

    async def text_deltas(self) -> AsyncIterator[str]:
        """Yield text delta payloads from token stream events."""
        async for event in self.events():
            if event.event != RuntimeEventType.TOKEN_DELTA.value:
                continue
            delta = (
                event.data.get("delta_text")
                or event.data.get("delta")
                or event.data.get("text")
            )
            if isinstance(delta, str) and delta:
                yield delta

    async def final_output(self) -> AgentRunOutput:
        """Await and return the final output for the streamed run."""
        async for _event in self.events():
            pass
        return await self._handle.final()

    def cancel(self, reason: str = "sdk_stream_cancel") -> None:
        """Request cancellation of the underlying run."""
        self._handle.abort(reason=reason)


__all__ = ["RunHandle", "RunStream"]
