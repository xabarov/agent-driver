"""SDK wiring for the opt-in Stage 2 quality gate."""

from types import SimpleNamespace

from agent_driver.llm import FakeProvider
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


def test_create_agent_wires_quality_gate_without_mutating_default_path() -> None:
    gate = SimpleNamespace()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        quality_gate=gate,
    )

    assert agent.runner.config.quality_gate is gate


def test_create_agent_quality_gate_is_inert_by_default() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
    )

    assert agent.runner.config.quality_gate is None
