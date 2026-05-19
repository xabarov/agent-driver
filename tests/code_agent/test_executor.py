"""CodeAgent executor tests."""

from __future__ import annotations

import pytest

from agent_driver.code_agent import (
    CodeAgentAction,
    CodeAgentLimits,
    CodeExecutionError,
    FakeRestrictedCodeExecutor,
)


@pytest.mark.asyncio
async def test_executor_runs_arithmetic_and_final_answer() -> None:
    """Executor should return final answer from helper call."""
    executor = FakeRestrictedCodeExecutor()
    result = await executor.execute(
        action=CodeAgentAction(
            action_id="a1", code="value = 2 + 3\nfinal_answer(value)"
        ),
        limits=CodeAgentLimits(),
        authorized_imports=set(),
        serialization_policy=None,
        callable_tools={},
    )
    assert result.final_answer is not None
    assert result.final_answer.text == "5"


@pytest.mark.asyncio
async def test_executor_captures_stdout_observation() -> None:
    """Executor should capture stdout as bounded observation."""
    executor = FakeRestrictedCodeExecutor()
    result = await executor.execute(
        action=CodeAgentAction(
            action_id="a2", code="print('hello from code agent')\nfinal_answer('ok')"
        ),
        limits=CodeAgentLimits(max_output_chars=8),
        authorized_imports=set(),
        serialization_policy=None,
        callable_tools={},
    )
    assert result.observations
    assert result.observations[0].source == "stdout"
    assert result.observations[0].truncated is True
    assert result.tool_results == []


@pytest.mark.asyncio
async def test_executor_blocks_unauthorized_import() -> None:
    """Unauthorized import should raise code execution error."""
    executor = FakeRestrictedCodeExecutor()
    with pytest.raises(CodeExecutionError):
        await executor.execute(
            action=CodeAgentAction(action_id="a3", code="import os\nfinal_answer('x')"),
            limits=CodeAgentLimits(),
            authorized_imports={"math"},
            serialization_policy=None,
            callable_tools={},
        )


@pytest.mark.asyncio
async def test_executor_rejects_async_callable_tools_in_process_mode() -> None:
    """Local executor should fail closed for awaitable tool handlers."""
    executor = FakeRestrictedCodeExecutor()

    async def _async_tool(_args):
        return {"summary": "ok"}

    with pytest.raises(CodeExecutionError, match="async tool handlers"):
        await executor.execute(
            action=CodeAgentAction(action_id="a4", code="demo_tool()"),
            limits=CodeAgentLimits(),
            authorized_imports=set(),
            serialization_policy=None,
            callable_tools={"demo_tool": _async_tool},
        )
