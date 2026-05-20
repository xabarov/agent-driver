"""Tests for interactive terminal chat CLI module."""

from __future__ import annotations

import asyncio

import pytest

from agent_driver.cli.chat import parse_chat_command, render_chat_stream, run_chat_session
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
from agent_driver.code_agent.backends.local import LocalPythonBackend
from agent_driver.code_agent.contracts import CodeAgentLimits
from agent_driver.runtime.events import InMemoryEventLog
from agent_driver.runtime.checkpoints import InMemoryCheckpointStore
from agent_driver.runtime.single_agent.config_sections import PythonToolSettings
from agent_driver.runtime.single_agent.types import RunnerConfig
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


class _InterruptIoHarness(_IoHarness):
    def __init__(self, events: list[str | BaseException]) -> None:
        self._inputs = iter(events)
        self.output: list[str] = []

    def read(self, _prompt: str) -> str:
        item = next(self._inputs)
        if isinstance(item, BaseException):
            raise item
        return item


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


class _HistoryEchoProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="unused")
        self.message_counts: list[int] = []

    async def stream(self, request: LlmRequest):
        self.message_counts.append(len(request.messages))
        yield LlmStreamEvent(event="delta", delta_text=f"seen={len(request.messages)}")
        yield LlmStreamEvent(
            event="done",
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="history-echo", model_name="fake-model"),
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
    assert "tools_used=" not in text
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
    _assistant, _in_tokens, _out_tokens, _pressure, _plan = await render_chat_stream(
        stream=_stream(), output=output.append, run_id="run_x"
    )
    text = "".join(output)
    assert "tool>" not in text
    assert "warn> warning kind=sample" in text


@pytest.mark.asyncio
async def test_chat_stream_tool_card_shows_args() -> None:
    events = [
        RunStreamEvent(
            stream_id="run_args:1",
            run_id="run_args",
            attempt_id="a1",
            seq=1,
            event="tool_call_started",
            data={
                "tools": [
                    {
                        "tool_name": "glob_search",
                        "tool_call_id": "call_1",
                        "args": {"pattern": "**/*", "max_results": 200},
                    }
                ]
            },
        ),
        RunStreamEvent(
            stream_id="run_args:2",
            run_id="run_args",
            attempt_id="a1",
            seq=2,
            event="tool_call_completed",
            data={
                "tools": [
                    {
                        "tool_name": "glob_search",
                        "tool_call_id": "call_1",
                        "status": "completed",
                        "result_summary": "200 paths matched",
                    }
                ]
            },
        ),
    ]

    async def _stream():
        for item in events:
            yield item

    output: list[str] = []
    await render_chat_stream(stream=_stream(), output=output.append, run_id="run_args")
    text = "".join(output)
    assert "tool> glob_search(pattern=**/*, max_results=200)" in text


@pytest.mark.asyncio
async def test_chat_stream_tool_card_shows_truncated_flag() -> None:
    events = [
        RunStreamEvent(
            stream_id="run_trunc:1",
            run_id="run_trunc",
            attempt_id="a1",
            seq=1,
            event="tool_call_started",
            data={
                "tools": [
                    {
                        "tool_name": "glob_search",
                        "tool_call_id": "call_1",
                        "args": {"pattern": "**/*", "max_results": 200},
                    }
                ]
            },
        ),
        RunStreamEvent(
            stream_id="run_trunc:2",
            run_id="run_trunc",
            attempt_id="a1",
            seq=2,
            event="tool_call_completed",
            data={
                "tools": [
                    {
                        "tool_name": "glob_search",
                        "tool_call_id": "call_1",
                        "status": "completed",
                        "result_summary": "200 paths matched",
                        "truncated": True,
                    }
                ]
            },
        ),
    ]

    async def _stream():
        for item in events:
            yield item

    output: list[str] = []
    await render_chat_stream(stream=_stream(), output=output.append, run_id="run_trunc")
    text = "".join(output)
    assert "truncated=true" in text


@pytest.mark.asyncio
async def test_chat_stream_tool_card_shows_glob_preview_paths() -> None:
    events = [
        RunStreamEvent(
            stream_id="run_glob_preview:1",
            run_id="run_glob_preview",
            attempt_id="a1",
            seq=1,
            event="tool_call_completed",
            data={
                "tools": [
                    {
                        "tool_name": "glob_search",
                        "tool_call_id": "call_1",
                        "status": "completed",
                        "result_summary": "2 paths matched '*.md'",
                        "result_preview_paths": ["README.md", "docs/README.md"],
                    }
                ]
            },
        )
    ]

    async def _stream():
        for item in events:
            yield item

    output: list[str] = []
    await render_chat_stream(
        stream=_stream(), output=output.append, run_id="run_glob_preview"
    )
    text = "".join(output)
    assert "sample=README.md, docs/README.md" in text


@pytest.mark.asyncio
async def test_chat_stream_tool_card_shows_web_preview_urls() -> None:
    events = [
        RunStreamEvent(
            stream_id="run_web_preview:1",
            run_id="run_web_preview",
            attempt_id="a1",
            seq=1,
            event="tool_call_completed",
            data={
                "tools": [
                    {
                        "tool_name": "web_search",
                        "tool_call_id": "call_1",
                        "status": "completed",
                        "result_summary": "2 results for 'sam3' via duckduckgo_html",
                        "result_preview_paths": [
                            "https://ai.meta.com/blog/segment-anything-model-3/",
                            "https://www.infoq.com/news/2025/11/meta-sam3/",
                        ],
                    }
                ]
            },
        )
    ]

    async def _stream():
        for item in events:
            yield item

    output: list[str] = []
    await render_chat_stream(
        stream=_stream(), output=output.append, run_id="run_web_preview"
    )
    text = "".join(output)
    assert (
        "sample_urls=https://ai.meta.com/blog/segment-anything-model-3/, "
        "https://www.infoq.com/news/2025/11/meta-sam3/" in text
    )


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
    assert "tool> web_search(" in text
    assert "summary=" in text
    assert "event> run run_failed reason=tool_policy_denied" in text
    assert "hint> Check --max-tool-calls and tool policy." in text


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
            "/doctor",
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
    assert "doctor> tools " in text
    assert "doctor> last_signal final_answered" in text
    assert "session> " in text
    assert (tmp_path / "saved-session.json").exists()
    sessions = store.list_sessions()
    assert sessions


@pytest.mark.asyncio
async def test_chat_passes_transcript_as_messages() -> None:
    event_log = InMemoryEventLog()
    provider = _HistoryEchoProvider()
    agent = create_agent(
        provider=provider,
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["hello", "again", "/exit"])
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert code == 0
    assert provider.message_counts[:2] == [2, 4]


@pytest.mark.asyncio
async def test_chat_reset_clears_memory() -> None:
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["hello", "/reset", "/history", "/exit"])
    selected_manifests = [row.manifest for row in agent.runner.deps.tool_registry.list_registered()]
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=selected_manifests,
        input_reader=io.read,
        output=io.write,
    )
    assert code == 0
    text = "".join(io.output)
    assert "chat> memory reset thread=thread_" in text
    assert "history> empty" in text


def test_main_chat_command_with_monkeypatched_input(monkeypatch, capsys) -> None:
    """Main chat command should run interactive loop and exit cleanly."""
    lines = iter(["hello", "/exit"])
    monkeypatch.setattr("builtins.input", lambda _prompt: next(lines))
    exit_code = main(["chat", "--plain", "--provider", "fake"])
    assert exit_code == 0
    output = capsys.readouterr().out
    assert "chat> session=" in output
    assert "assistant>" in output


@pytest.mark.asyncio
async def test_chat_session_supports_bang_shell_command() -> None:
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _IoHarness(["!printf hello", "/exit"])
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=[],
        input_reader=io.read,
        output=io.write,
    )
    assert code == 0
    text = "".join(io.output)
    assert "● Bash(!printf hello)" in text
    assert "⎿ hello" in text


@pytest.mark.asyncio
async def test_chat_session_double_ctrl_c_exits() -> None:
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.only(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
    )
    io = _InterruptIoHarness([KeyboardInterrupt(), KeyboardInterrupt()])
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=[],
        input_reader=io.read,
        output=io.write,
    )
    assert code == 0
    text = "".join(io.output)
    assert "press Ctrl+C again within 2s to exit" in text
    assert "chat> interrupted" in text


@pytest.mark.asyncio
async def test_chat_session_exit_shuts_down_python_workers() -> None:
    """Exiting chat must terminate local python-tool workers to avoid process hang."""
    event_log = InMemoryEventLog()
    agent = create_agent(
        provider=FakeProvider(response_text="ok"),
        tools=ToolSet.all(),
        checkpoint_store=InMemoryCheckpointStore(),
        event_log=event_log,
        config=RunnerConfig(
            python_tool=PythonToolSettings(
                enabled=True,
                backend="local",
                session_idle_seconds=300.0,
            )
        ),
    )
    backend = agent.runner.deps.python_backend
    assert isinstance(backend, LocalPythonBackend)
    await backend.execute(
        code="value = 1",
        session_id="chat_exit_test",
        authorized_imports=set(),
        limits=CodeAgentLimits(max_exec_ms=300),
        serialization_policy=None,
    )
    assert backend._sessions
    io = _IoHarness(["/exit"])
    code = await run_chat_session(
        agent=agent,
        event_log=event_log,
        agent_id="agent.cli",
        graph_preset="single_react",
        stream_poll_interval_ms=10,
        selected_manifests=[],
        input_reader=io.read,
        output=io.write,
        ui_mode="plain",
    )
    assert code == 0
    assert not backend._sessions
    assert "chat> bye" in "".join(io.output)
