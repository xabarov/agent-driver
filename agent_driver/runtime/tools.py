"""Minimal tool execution protocol for runtime step loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Protocol

from agent_driver.contracts.interrupts import InterruptRequest
from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolResultEnvelope, ToolTrace
from agent_driver.llm.contracts import LlmResponse
from agent_driver.runtime.tool_gate import ToolGate


@dataclass(slots=True)
class ToolExecutionResult:
    """Result envelope for one tool execution stage."""

    traces: list[ToolTrace] = field(default_factory=list)
    envelopes: list[ToolResultEnvelope] = field(default_factory=list)
    interrupt: InterruptRequest | None = None


# Executors accept an optional ``tool_gate`` kwarg added in A0.2. Older
# executors that ignore the kwarg keep working because we use ``...`` in
# the type alias rather than fixing the signature — the public protocol
# below documents the contract.
ToolExecutor = Callable[..., Awaitable[ToolExecutionResult]]


class GovernedExecutionLike(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal result shape produced by governed tool execution."""

    traces: list[ToolTrace]
    envelopes: list[ToolResultEnvelope]
    interrupt: InterruptRequest | None


class GovernedExecutorLike(Protocol):  # pylint: disable=too-few-public-methods
    """Minimal governed executor protocol expected by runtime adapter."""

    async def execute(
        self,
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        tool_gate: ToolGate | None = None,
    ) -> GovernedExecutionLike:
        """Execute governed policy/guardrail/tool pipeline for one step."""
        raise NotImplementedError


async def fake_noop_tool_executor(
    run_input: AgentRunInput,
    llm_response: LlmResponse,
    *,
    tool_gate: ToolGate | None = None,
) -> ToolExecutionResult:
    """Default no-op tool executor used before full tool governance."""
    _ = (run_input, llm_response, tool_gate)
    return ToolExecutionResult()


def wrap_governed_executor(executor: GovernedExecutorLike) -> ToolExecutor:
    """Adapt governed executor to runtime ToolExecutor protocol."""

    async def _run(
        run_input: AgentRunInput,
        llm_response: LlmResponse,
        *,
        tool_gate: ToolGate | None = None,
    ) -> ToolExecutionResult:
        governed = await executor.execute(
            run_input, llm_response, tool_gate=tool_gate
        )
        return ToolExecutionResult(
            traces=governed.traces,
            envelopes=governed.envelopes,
            interrupt=governed.interrupt,
        )

    return _run
