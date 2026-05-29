"""Optional live smoke tests (split by concern)."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts import AgentRunInput, ResumeAction, ResumeCommand, ToolCall, ToolRisk
from tests.support.live_harness import (
    assert_live_interrupt_for_tool,
    build_live_runner,
    notebook_fixture,
    require_live_openrouter_config,
    tool_result,
)


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_bash_call() -> None:
    """Run live LLM call plus real governed bash tool execution."""
    base_url, model, api_key = require_live_openrouter_config()
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about shell verification.",
            run_id="run_live_agent_tool_bash_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash",
                            args={
                                "command": "echo live-bash-smoke",
                                "timeout_seconds": 5,
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = tool_result(output, "bash")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("exit_code") == 0
    assert structured.get("timed_out") is False
    stdout = str(structured.get("stdout") or "")
    assert "live-bash-smoke" in stdout
    tool_trace = output.tool_trace
    assert any(
        item.tool_name == "bash" and item.status.value == "completed"
        for item in tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_notebook_edit_call(
    tmp_path,
) -> None:
    """Run live LLM call plus real notebook_edit on temp .ipynb."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "live_notebook.ipynb"
    notebook_fixture(target)
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about notebook edit verification.",
            run_id="run_live_agent_tool_notebook_edit_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="notebook_edit",
                            args={
                                "path": str(target),
                                "cell_idx": 0,
                                "is_new_cell": False,
                                "old_text": "old",
                                "new_text": "new",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = tool_result(output, "notebook_edit")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("operation") == "replace"
    rendered = json.loads(target.read_text(encoding="utf-8"))
    assert rendered["cells"][0]["source"] == ["print('new')\n"]
    assert any(
        item.tool_name == "notebook_edit" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_file_write_call(tmp_path) -> None:
    """Run live LLM call plus real file_write side-effect on temp file."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "live_write.txt"
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about file write verification.",
            run_id="run_live_agent_tool_file_write_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_write",
                            args={"path": str(target), "content": "live-write\n"},
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = tool_result(output, "file_write")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("mode") == "overwrite"
    assert target.read_text(encoding="utf-8") == "live-write\n"
    assert any(
        item.tool_name == "file_write" and item.status.value == "completed"
        for item in output.tool_trace
    )


@pytest.mark.asyncio
async def test_live_agent_run_with_governed_builtin_file_edit_call(tmp_path) -> None:
    """Run live LLM call plus real file_edit side-effect on temp file."""
    base_url, model, api_key = require_live_openrouter_config()
    target = tmp_path / "live_edit.txt"
    target.write_text("alpha-old\n", encoding="utf-8")
    runner = build_live_runner(base_url=base_url, model=model, api_key=api_key)
    output = await runner.run(
        AgentRunInput(
            input="Reply with one short sentence about file edit verification.",
            run_id="run_live_agent_tool_file_edit_smoke",
            agent_id="agent.live",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="file_edit",
                            args={
                                "path": str(target),
                                "old_text": "old",
                                "new_text": "new",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )
    assert output.status.value == "completed"
    envelope = tool_result(output, "file_edit")
    assert envelope
    assert envelope["decision"] == "allow"
    structured = envelope.get("structured_output")
    assert isinstance(structured, dict)
    assert structured.get("replacements") == 1
    assert target.read_text(encoding="utf-8") == "alpha-new\n"
    assert any(
        item.tool_name == "file_edit" and item.status.value == "completed"
        for item in output.tool_trace
    )
