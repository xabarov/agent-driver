"""Runtime integration for skill invocation records."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput, RuntimeEventType, ToolCall
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


@pytest.mark.asyncio
async def test_skill_view_records_invocation_event_and_metadata(tmp_path) -> None:
    """Runtime should persist compact skill refs when skill_view loads a skill."""
    skill_file = tmp_path / "skills" / "alpha" / "SKILL.md"
    skill_file.parent.mkdir(parents=True)
    skill_file.write_text(
        """---
name: alpha
description: Alpha skill
---
# Alpha
""",
        encoding="utf-8",
    )
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only("skill_view"),
    )

    output = await agent.run(
        AgentRunInput(
            input="Load alpha skill.",
            run_id="run_skill_invoked",
            agent_id="agent",
            graph_preset="single_react",
            tool_policy={
                "metadata": {
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="skill_view",
                            tool_call_id="call_skill",
                            args={
                                "base_dir": str(tmp_path),
                                "name": "alpha",
                                "trusted_roots": [str(tmp_path / "skills")],
                                "agent_id": "agent",
                            },
                        ).model_dump(mode="json")
                    ]
                }
            },
        )
    )

    invocations = output.metadata.get("skill_invocations")
    refs = output.metadata.get("invoked_skill_refs")
    assert isinstance(invocations, list) and invocations
    assert isinstance(refs, list) and refs
    assert invocations[0]["name"] == "alpha"
    assert invocations[0]["tool_call_id"] == "call_skill"
    assert refs[0]["digest"] == invocations[0]["digest"]
    assert any(event.type == RuntimeEventType.SKILL_INVOKED for event in output.events)
