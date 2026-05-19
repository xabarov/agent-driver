"""Tests for interactive terminal chat CLI module."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.cli.chat import parse_chat_command, render_chat_stream, run_chat_session
from agent_driver.cli.prompt_icon import prompt_spinner_frame
from agent_driver.cli.sessions import SessionStore
from agent_driver.cli.main import main
from agent_driver.contracts import RunStreamEvent, ToolCall
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.contracts import (
    LlmFinishReason,
    LlmRequest,
    LlmResponse,
    LlmStreamEvent,
    UsageSummary,
)
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


class _LoopingChatProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ignored")
        self.complete_calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.complete_calls += 1
        return LlmResponse(
            message=ChatMessage(role="assistant", content=""),
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=UsageSummary(model_provider="loop-chat", model_name="loop-model"),
            provider="loop-chat",
            model="loop-model",
            metadata={
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="web_search",
                        args={
                            "query": "news",
                            "mock_results": [{"title": "A", "url": "https://example.com"}],
                        },
                    ).model_dump(mode="json")
                ]
            },
        )

    async def stream(self, request: LlmRequest):
        self.complete_calls += 1
        yield LlmStreamEvent(
            event="tool_calls",
            finish_reason=LlmFinishReason.TOOL_CALLS,
            usage=UsageSummary(model_provider="loop-chat", model_name="loop-model"),
            metadata={
                "planned_tool_calls": [
                    ToolCall(
                        tool_name="web_search",
                        args={
                            "query": "news",
                            "mock_results": [{"title": "A", "url": "https://example.com"}],
                        },
                    ).model_dump(mode="json")
                ]
            },
        )


class _ZeroResultFinalProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ignored")
        self.calls = 0

    async def stream(self, request: LlmRequest):
        self.calls += 1
        if self.calls == 1:
            yield LlmStreamEvent(
                event="tool_calls",
                finish_reason=LlmFinishReason.TOOL_CALLS,
                usage=UsageSummary(model_provider="zero-final", model_name="loop-model"),
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="web_search",
                            tool_call_id="call_zero_1",
                            args={"query": "none", "mock_results": []},
                        ).model_dump(mode="json")
                    ]
                },
            )
            return
        assert request.tool_choice == "none"
        yield LlmStreamEvent(event="delta", delta_text="Ничего не нашел по запросу.")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="zero-final", model_name="loop-model"),
        )


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
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "assistant> chat ok" in text
    assert "run> run_chat_" in text
    assert "tools_used=" in text
    assert "chat> bye" in text
    assert "node_completed" not in text


@pytest.mark.asyncio
async def test_chat_session_replay_tail_and_clear_commands() -> None:
    """Slash commands should read replay/tail and clear local session state."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="chat replay"),
        tools=ToolSet.packs("filesystem_read"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["hello", "/tools", "/tools verbose", "/replay", "/tail", "/clear", "/runs", "/exit"])
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "[0001] run_started:" in text
    assert "run_completed" in text
    assert "tools>" in text
    assert "risk=" in text
    assert "chat> cleared" in text
    assert "chat> no runs yet" in text


@pytest.mark.asyncio
async def test_chat_stream_formats_tool_events_compactly() -> None:
    """Renderer should map tool/warn events to compact prefixes."""
    events = [
        RunStreamEvent(
            stream_id="run_x:1",
            run_id="run_x",
            attempt_id="a1",
            seq=1,
            event="tool_call_started",
            data={"tool_name": "web_search"},
        ),
        RunStreamEvent(
            stream_id="run_x:2",
            run_id="run_x",
            attempt_id="a1",
            seq=2,
            event="warning",
            data={"kind": "sample"},
        ),
        RunStreamEvent(
            stream_id="run_x:3",
            run_id="run_x",
            attempt_id="a1",
            seq=3,
            event="run_completed",
            data={},
        ),
    ]

    async def _stream():
        for item in events:
            yield item

    output: list[str] = []
    _ = await render_chat_stream(stream=_stream(), output=output.append, run_id="run_x")
    text = "".join(output)
    assert "tool> tool tool_call_started tool=web_search" in text
    assert "warn> warning kind=sample" in text


def test_prompt_spinner_frame_is_blue_agent_icon() -> None:
    """Prompt spinner frame should render the branded blue prompt icon."""
    frame = prompt_spinner_frame(0)

    assert frame.startswith("\r\033[38;2;")
    assert "agent> thinking..." in frame
    assert frame.endswith("thinking...")


@pytest.mark.asyncio
async def test_chat_stream_animation_clears_before_answer() -> None:
    """Animated chat stream should clear spinner before assistant text."""

    async def _stream():
        await asyncio.sleep(0.02)
        yield RunStreamEvent(
            stream_id="run_x:1",
            run_id="run_x",
            attempt_id="a1",
            seq=1,
            event="token_delta",
            data={"delta_text": "hi"},
        )
        yield RunStreamEvent(
            stream_id="run_x:2",
            run_id="run_x",
            attempt_id="a1",
            seq=2,
            event="run_completed",
            data={},
        )

    output: list[str] = []
    _ = await render_chat_stream(
        stream=_stream(),
        output=output.append,
        run_id="run_x",
        animate=True,
    )
    text = "".join(output)
    assert "agent> thinking..." in text
    assert "\033[2Kassistant> hi" in text


@pytest.mark.asyncio
async def test_chat_session_looping_tool_calls_fail_by_budget_with_named_tools() -> None:
    """Looping tool calls should stop by budget and render named tool events."""
    event_log = InMemoryEventLog()
    provider = _LoopingChatProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only("web_search"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["новости китая", "/exit"])
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        max_tool_calls=2,
        max_steps=8,
        deadline_seconds=30.0,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "tool> tool start web_search" in text
    assert "tool> tool done web_search status=completed" in text
    assert "event> run run_failed reason=tool_policy_denied" in text
    assert "hint> run stopped by tool-call budget/policy" in text


@pytest.mark.asyncio
async def test_chat_session_zero_results_get_honest_final_answer() -> None:
    """Zero-result tool call should end with final assistant answer, not max-steps failure."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=_ZeroResultFinalProvider(),
        tools=ToolSet.only("web_search"),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["новости", "/exit"])
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    exit_code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        max_tool_calls=4,
        max_steps=8,
        deadline_seconds=30.0,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert exit_code == 0
    text = "".join(io.output)
    assert "assistant> Ничего не нашел по запросу." in text
    assert "run_failed" not in text


@pytest.mark.asyncio
async def test_chat_session_supports_sessions_history_and_debug_commands(tmp_path) -> None:
    """Chat slash commands should expose session/history/debug and persist session state."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    store = SessionStore(path=tmp_path / "sessions.json")
    io = _IoHarness(
        [
            "hello",
            "/history",
            "/model",
            "/provider",
            "/limits",
            "/debug on",
            f"/save {tmp_path / 'saved-session.json'}",
            "/sessions",
            "/exit",
        ]
    )
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        session_store=store,
        provider_name="fake",
        model_name="fake-model",
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert code == 0
    text = "".join(io.output)
    assert "history>" not in text  # rendered as role-prefixed rows
    assert "user> hello" in text
    assert "model> fake-model" in text
    assert "provider> fake" in text
    assert "limits> " in text
    assert "debug> on" in text
    assert "session> " in text
    assert (tmp_path / "saved-session.json").exists()
    sessions = store.list_sessions()
    assert sessions


def test_main_chat_command_with_monkeypatched_input(monkeypatch, capsys) -> None:
    """Main chat command should run interactive loop and exit cleanly."""
    lines = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(lines))
    exit_code = main(["chat", "--plain", "--provider", "fake"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "chat> session=" in output
    assert "assistant>" in output
