"""Minimal tool execution protocol for runtime step loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace
from agent_driver.llm.contracts import LlmResponse
from agent_driver.tools.executor import GovernedToolExecutor


@dataclass(slots=True)
class ToolExecutionResult:
    """Result envelope for one tool execution stage."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None


ToolExecutor = Callable[[AgentRunInput, LlmResponse], Awaitable[ToolExecutionResult]]


async def fake_noop_tool_executor(
    run_input: AgentRunInput, llm_response: LlmResponse
) -> ToolExecutionResult:
    """Default no-op tool executor used before full tool governance."""
    _ = (run_input, llm_response)
    return ToolExecutionResult()


def wrap_governed_executor(executor: GovernedToolExecutor) -> ToolExecutor:
    """Adapt governed executor to runtime ToolExecutor protocol."""

    async def _run(
        run_input: AgentRunInput, llm_response: LlmResponse
    ) -> ToolExecutionResult:
        governed = await executor.execute(run_input, llm_response)
        return ToolExecutionResult(
            traces=governed.traces,
            envelopes=governed.envelopes,
            interrupt=governed.interrupt,
        )

    return _run
