"""Governed tool execution aggregate result."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace
from agent_driver.tools.context import ToolProgress


@dataclass(slots=True)
class ProgressEntry:
    """One progress update captured during a tool call.

    Phase 11 H16 — collected in execution order; carries the originating
    ``call_index`` (1-based, matches trace order) so consumers can
    correlate progress with the final trace/envelope.
    """

    call_index: int
    tool_name: str
    progress: ToolProgress


@dataclass(slots=True)
class GovernedExecutionResult:
    """Detailed executor result used for runtime integration."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None
    # Phase 11 H16 — intermediate progress events emitted during tool
    # execution. The runtime projector converts each entry into a
    # ``RuntimeEventType.TOOL_PROGRESS`` event in order.
    progress_events: list[ProgressEntry] = field(default_factory=list)

    def append(
        self,
        *,
        envelope: ToolResultEnvelope,
        trace: ToolTrace,
        interrupt: InterruptRequest | None = None,
    ) -> None:
        """Append envelope/trace pair and optional interrupt."""
        self.envelopes.append(envelope)
        self.traces.append(trace)
        if interrupt is not None:
            self.interrupt = interrupt

    def record_progress(
        self,
        *,
        call_index: int,
        tool_name: str,
        progress: ToolProgress,
    ) -> None:
        """Append one progress update from an in-flight tool handler."""
        self.progress_events.append(
            ProgressEntry(
                call_index=call_index,
                tool_name=tool_name,
                progress=progress,
            )
        )
