"""Agents-as-tools and handoffs (coordination C6)."""

from __future__ import annotations

import pytest

import agent_driver.sdk.agent_tool as agent_tool
from agent_driver.agents.definition import AgentDefinition
from agent_driver.contracts.enums import RunStatus
from agent_driver.sdk import SubagentSpec, agent_as_tool, handoff_tool
from agent_driver.sdk.subagent import SubagentResult
from agent_driver.tools.custom import CustomToolDefinition

_CALLS: list[SubagentSpec] = []


def _result(agent_type: str, answer: str) -> SubagentResult:
    return SubagentResult(
        child_run_id="c",
        parent_run_id="p",
        agent_type=agent_type,
        status=RunStatus.COMPLETED,
        terminal_reason=None,
        answer=answer,
        structured_output=None,
        tool_trace=(),
        usage=None,
        raw_output=None,
    )


async def _fake_run_subagent(parent, spec, **_kw):  # noqa: ANN001, ANN201
    _CALLS.append(spec)
    return _result(spec.agent_type, f"answer for: {spec.prompt}")


@pytest.fixture(autouse=True)
def _patch(monkeypatch: pytest.MonkeyPatch) -> None:
    _CALLS.clear()
    monkeypatch.setattr(agent_tool, "run_subagent", _fake_run_subagent)


@pytest.mark.asyncio
async def test_agent_as_tool_from_spec_runs_specialist_with_input_prompt() -> None:
    spec = SubagentSpec(agent_type="researcher", prompt="TEMPLATE")
    tool = agent_as_tool(None, spec)
    assert isinstance(tool, CustomToolDefinition)
    assert tool.manifest.name == "ask_researcher"
    assert tool.manifest.args_schema["required"] == ["input"]
    assert tool.manifest.metadata["handoff"] is False

    out = await tool.handler({"input": "find pricing data"})
    assert out == {
        "agent": "researcher",
        "status": "completed",
        "answer": "answer for: find pricing data",
        "handoff": False,
    }
    # the per-call prompt is the model input, not the template
    assert _CALLS[0].prompt == "find pricing data"
    assert _CALLS[0].agent_type == "researcher"


@pytest.mark.asyncio
async def test_handoff_tool_names_and_marks_transfer() -> None:
    spec = SubagentSpec(agent_type="writer", prompt="x")
    tool = handoff_tool(None, spec)
    assert tool.manifest.name == "transfer_to_writer"
    assert tool.manifest.metadata["handoff"] is True
    assert "final answer" in tool.manifest.description.lower()

    out = await tool.handler({"input": "write the report"})
    assert out["handoff"] is True
    assert out["answer"] == "answer for: write the report"


@pytest.mark.asyncio
async def test_agent_as_tool_from_definition_uses_name_and_when_to_use() -> None:
    definition = AgentDefinition(
        name="data_explorer",
        description="Explores data.",
        when_to_use="Use when the task needs data discovery.",
        system_prompt="You explore data.",
    )
    tool = agent_as_tool(None, definition)
    assert tool.manifest.name == "ask_data_explorer"
    assert "data discovery" in tool.manifest.description

    await tool.handler({"input": "profile the sheet"})
    # the definition became a spec whose prompt is the per-call input
    assert _CALLS[0].agent_type == "data_explorer"
    assert _CALLS[0].prompt == "profile the sheet"


@pytest.mark.asyncio
async def test_custom_name_and_input_arg() -> None:
    spec = SubagentSpec(agent_type="q", prompt="x")
    tool = agent_as_tool(None, spec, name="consult_expert", input_arg="question")
    assert tool.manifest.name == "consult_expert"
    assert tool.manifest.args_schema["required"] == ["question"]
    await tool.handler({"question": "why?"})
    assert _CALLS[0].prompt == "why?"


@pytest.mark.asyncio
async def test_tool_name_is_sanitized() -> None:
    spec = SubagentSpec(agent_type="weird / name!!", prompt="x")
    tool = agent_as_tool(None, spec)
    # name must satisfy ToolManifest's [A-Za-z0-9_.:-]+ rule (no spaces/slashes)
    assert tool.manifest.name == "ask_weird_name"


@pytest.mark.asyncio
async def test_empty_input_still_runs_with_empty_prompt() -> None:
    spec = SubagentSpec(agent_type="a", prompt="x")
    tool = agent_as_tool(None, spec)
    out = await tool.handler({})
    assert _CALLS[0].prompt == ""
    assert out["answer"] == "answer for: "


def test_rejects_bad_spec_type() -> None:
    tool = agent_as_tool(None, SubagentSpec(agent_type="a", prompt="x"))
    with pytest.raises(TypeError):
        # _spec_for is exercised via the handler; call it directly for the type guard
        agent_tool._spec_for(object(), "p")  # type: ignore[arg-type]
    assert tool.manifest.name == "ask_a"
