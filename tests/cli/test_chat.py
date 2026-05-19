"""Tests for interactive terminal chat CLI module."""

from __future__ import annotations

import pytest

from agent_driver.cli.chat import parse_chat_command, run_chat_session
from agent_driver.cli.main import main
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.sdk import create_agent
from agent_driver.tools import ToolSet


class _IoHarness:
    def __init__(self, inputs: list[str]) -> None:
        self._inputs = iter(inputs)
        self.output: list[str] = []

    def read(self, _prompt: str) -> str:
        try:
            return next(self._inputs)
        except StopIteration as exc:
            raise EOFError from exc

    def write(self, text: str) -> None:
        self.output.append(text)


def test_parse_chat_command() -> None:
    """Slash parser should split command and args."""
    assert parse_chat_command("hello") is None
    assert parse_chat_command("/help") == ("help", [])
    assert parse_chat_command("/tail run_1 10") == ("tail", ["run_1", "10"])


@pytest.mark.asyncio
async def test_chat_session_basic_turn_and_runs_listing() -> None:
    """Chat session should stream one turn and list run ids."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="chat ok"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["hello", "/runs", "/exit"])
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "assistant> chat ok" in text
    assert "run> run_chat_" in text
    assert "chat> bye" in text


@pytest.mark.asyncio
async def test_chat_session_replay_tail_and_clear_commands() -> None:
    """Slash commands should read replay/tail and clear local session state."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="chat replay"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["hello", "/replay", "/tail", "/clear", "/runs", "/exit"])
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "[0001] run_started:" in text
    assert "run_completed" in text
    assert "chat> cleared" in text
    assert "chat> no runs yet" in text


def test_main_chat_command_with_monkeypatched_input(monkeypatch, capsys) -> None:
    """Main chat command should run interactive loop and exit cleanly."""
    lines = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(lines))
    exit_code = main(["chat", "--plain", "--provider", "fake"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "chat> session=" in output
    assert "assistant>" in output
