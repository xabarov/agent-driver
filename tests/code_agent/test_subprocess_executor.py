"""Subprocess executor tests for hard timeout and contract conformance."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import (
    CodeAgentAction,
    CodeAgentLimits,
    CodeExecutionError,
    SubprocessRestrictedCodeExecutor,
)


@pytest.mark.asyncio
async def test_subprocess_executor_returns_final_answer() -> None:
    """Subprocess executor should return final answer for simple action."""
    executor = SubprocessRestrictedCodeExecutor()
    result = await executor.execute(
        action=CodeAgentAction(action_id="sp_1", code="final_answer(10 + 2)"),
        limits=CodeAgentLimits(max_exec_ms=500),
        authorized_imports=set(),
        serialization_policy=None,
        callable_tools={},
    )
    assert result.final_answer is not None
    assert result.final_answer.text == "12"
    assert result.metadata["executor_mode"] == "subprocess"


@pytest.mark.asyncio
async def test_subprocess_executor_enforces_timeout() -> None:
    """Infinite loop should be terminated by subprocess timeout."""
    executor = SubprocessRestrictedCodeExecutor()
    with pytest.raises(CodeExecutionError, match="execution time limit exceeded"):
        await executor.execute(
            action=CodeAgentAction(
                action_id="sp_2",
                code="while True:\n    pass",
            ),
            limits=CodeAgentLimits(max_exec_ms=50),
            authorized_imports=set(),
            serialization_policy=None,
            callable_tools={},
        )


@pytest.mark.asyncio
async def test_subprocess_executor_falls_back_for_callable_tools() -> None:
    """When tools are present subprocess executor should use local fallback."""
    executor = SubprocessRestrictedCodeExecutor()

    def _calc(args):
        return int(args["value"]) + 1

    result = await executor.execute(
        action=CodeAgentAction(action_id="sp_3", code="calc(value=5)\nfinal_answer(3)"),
        limits=CodeAgentLimits(max_exec_ms=500),
        authorized_imports=set(),
        serialization_policy=None,
        callable_tools={"calc": _calc},
    )
    assert result.final_answer is not None
    assert result.final_answer.text == "3"
    assert result.metadata["executor_mode"] == "local_fallback"
