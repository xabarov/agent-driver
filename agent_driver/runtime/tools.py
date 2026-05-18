"""Minimal tool execution protocol for runtime step loop."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field

from agent_driver.contracts.runtime import AgentRunInput
from agent_driver.contracts.tools import ToolTrace
from agent_driver.llm.contracts import LlmResponse


@dataclass(slots=True)
class ToolExecutionResult:
    """Result envelope for one tool execution stage."""

    traces: list[ToolTrace] = field(default_factory=list)


ToolExecutor = Callable[[AgentRunInput, LlmResponse], Awaitable[ToolExecutionResult]]


async def fake_noop_tool_executor(
    run_input: AgentRunInput, llm_response: LlmResponse
) -> ToolExecutionResult:
    """Default no-op tool executor used before full tool governance."""
    _ = (run_input, llm_response)
    return ToolExecutionResult()
