"""Minimal tool execution protocol for runtime step loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace
from agent_driver.llm.contracts import LlmResponse


@dataclass(slots=True)
class ToolExecutionResult:
    """Result envelope for one tool execution stage."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None


ToolExecutor = Callable[[AgentRunInput, LlmResponse], Awaitable[ToolExecutionResult]]


class GovernedExecutionLike(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal result shape produced by governed tool execution."""

    traces: list[ToolTrace]
    envelopes: list[ToolResultEnvelope]
    interrupt: InterruptRequest | None


class GovernedExecutorLike(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal governed executor protocol expected by runtime adapter."""

    async def execute(
        self, run_input: AgentRunInput, llm_response: LlmResponse
    ) -> GovernedExecutionLike:
        """Execute governed policy/guardrail/tool pipeline for one step."""
        raise NotImplementedError


async def fake_noop_tool_executor(
    run_input: AgentRunInput, llm_response: LlmResponse
) -> ToolExecutionResult:
    """Default no-op tool executor used before full tool governance."""
    _ = (run_input, llm_response)
    return ToolExecutionResult()


def wrap_governed_executor(executor: GovernedExecutorLike) -> ToolExecutor:
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
