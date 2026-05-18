"""Governed tool execution aggregate result."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace


@dataclass(slots=True)
class GovernedExecutionResult:
    """Detailed executor result used for runtime integration."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None

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
