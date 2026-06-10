"""Offline tests for the ACP adapter driven by a fake ACP client.

No editor and no network: a :class:`FakeAcpClient` records every
``session_update`` and answers ``request_permission`` deterministically, while
``FakeProvider`` drives the runtime. Tool approvals are forced with a tiny
always-ASK :func:`_ask_gate` so a *safe* command (``echo hi``) still parks the
run on an interrupt — we never execute a risky command to exercise the
approval path.
"""

from __future__ import annotations

from typing import Any

import pytest
from acp import schema

from agent_driver.adapters.acp import AgentAcpServer
from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import ToolGateAsk, ToolGateContext
from agent_driver.sdk import ToolSet, create_agent


async def _ask_gate(_context: ToolGateContext) -> ToolGateAsk:
    """A gate that pauses every planned call for operator approval."""
    return ToolGateAsk(message="Approve this tool call?")


class FakeAcpClient:
    """Records session updates and answers permission requests deterministically."""

    def __init__(
        self, *, allow_option_id: str | None = None, on_permission: Any = None
    ) -> None:
        self.updates: list[Any] = []
        self.permission_requests: list[dict[str, Any]] = []
        self._allow_option_id = allow_option_id
        self._on_permission = on_permission

        self.fs_reads: list[str] = []
        self.fs_writes: list[tuple[str, str]] = []
        self.read_content = "EDITOR BUFFER CONTENT"
        self.terminal_events: list[tuple[str, str]] = []
        self.terminal_output_text = "terminal stdout\n"
        self.terminal_exit_code = 0

    async def session_update(self, *, session_id: str, update: Any, **_: Any) -> None:
        self.updates.append(update)

    async def read_text_file(
        self, *, path: str, session_id: str, **_: Any
    ) -> schema.ReadTextFileResponse:
        self.fs_reads.append(path)
        return schema.ReadTextFileResponse(content=self.read_content)

    async def write_text_file(
        self, *, content: str, path: str, session_id: str, **_: Any
    ) -> None:
        self.fs_writes.append((path, content))
        return None

    async def create_terminal(
        self, *, command: str, session_id: str, cwd: Any = None, **_: Any
    ) -> schema.CreateTerminalResponse:
        self.terminal_events.append(("create", command))
        return schema.CreateTerminalResponse(terminal_id="term-1")

    async def wait_for_terminal_exit(
        self, *, session_id: str, terminal_id: str, **_: Any
    ) -> schema.WaitForTerminalExitResponse:
        self.terminal_events.append(("wait", terminal_id))
        return schema.WaitForTerminalExitResponse(exit_code=self.terminal_exit_code)

    async def terminal_output(
        self, *, session_id: str, terminal_id: str, **_: Any
    ) -> schema.TerminalOutputResponse:
        self.terminal_events.append(("output", terminal_id))
        return schema.TerminalOutputResponse(
            output=self.terminal_output_text, truncated=False
        )

    async def release_terminal(
        self, *, session_id: str, terminal_id: str, **_: Any
    ) -> None:
        self.terminal_events.append(("release", terminal_id))
        return None

    async def request_permission(
        self, *, options: list[Any], session_id: str, tool_call: Any, **_: Any
    ) -> schema.RequestPermissionResponse:
        self.permission_requests.append({"options": options, "tool_call": tool_call})
        if self._on_permission is not None:
            await self._on_permission(session_id)
        if self._allow_option_id is None:
            return schema.RequestPermissionResponse(
                outcome=schema.DeniedOutcome(outcome="cancelled")
            )
        return schema.RequestPermissionResponse(
            outcome=schema.AllowedOutcome(
                option_id=self._allow_option_id, outcome="selected"
            )
        )

    def message_text(self) -> str:
        return "".join(
            u.content.text
            for u in self.updates
            if type(u).__name__ == "AgentMessageChunk"
        )

    def tool_titles(self) -> list[str]:
        return [
            u.title
            for u in self.updates
            if type(u).__name__ in {"ToolCallStart", "ToolCall"}
        ]


class _BashThenFinish(FakeProvider):
    """Plan a (safe) bash call on turn 1, then answer on the next turn."""

    def __init__(self) -> None:
        super().__init__(response_text="all done")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="bash-then-finish",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash",
                            args={"command": "echo hi"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


def _gated_agent() -> Any:
    """Agent whose every tool call parks for approval via :func:`_ask_gate`."""
    return create_agent(
        provider=_BashThenFinish(),
        tools=ToolSet.only("bash"),
        tool_gate=_ask_gate,
    )


async def _bind(server: AgentAcpServer, client: FakeAcpClient) -> str:
    server.on_connect(client)
    new_session = await server.new_session(cwd="/tmp")
    return new_session.session_id


@pytest.mark.asyncio
async def test_initialize_advertises_capabilities() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    server = AgentAcpServer(agent, name="agent-driver", version="9.9.9")
    resp = await server.initialize(protocol_version=1)
    assert resp.agent_info.name == "agent-driver"
    assert resp.agent_info.version == "9.9.9"
    assert resp.agent_capabilities.prompt_capabilities.image is False
    # Session richness: load_session + resume are advertised.
    assert resp.agent_capabilities.load_session is True
    assert resp.agent_capabilities.session_capabilities.resume is not None


@pytest.mark.asyncio
async def test_new_session_advertises_modes() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    server = AgentAcpServer(agent)
    resp = await server.new_session(cwd="/tmp")
    mode_ids = {m.id for m in resp.modes.available_modes}
    assert {"default", "yolo", "standard", "strict"} <= mode_ids
    assert resp.modes.current_mode_id == "default"


@pytest.mark.asyncio
async def test_set_session_mode_yolo_overrides_default_gate() -> None:
    # The agent's construction-time gate ASKs for every tool; switching the
    # session to "yolo" must override it so the run completes with no approval.
    server = AgentAcpServer(_gated_agent())
    client = FakeAcpClient(allow_option_id=None)
    session_id = await _bind(server, client)

    await server.set_session_mode(session_id=session_id, mode_id="yolo")
    resp = await server.prompt(prompt=[_text_block("run echo")], session_id=session_id)

    assert client.permission_requests == []  # gate overridden -> never asked
    assert resp.stop_reason == "end_turn"
    assert "all done" in client.message_text()


@pytest.mark.asyncio
async def test_load_session_replays_transcript() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="the answer"), tools=ToolSet.only()
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    session_id = await _bind(server, client)

    await server.prompt(prompt=[_text_block("hello there")], session_id=session_id)
    client.updates.clear()

    resp = await server.load_session(session_id=session_id, cwd="/tmp")

    kinds = [type(u).__name__ for u in client.updates]
    # Full conversation replayed: the user turn then the assistant answer.
    assert "UserMessageChunk" in kinds
    assert "AgentMessageChunk" in kinds
    assert resp.modes.current_mode_id == "default"


class _PlanToolThenFinish(FakeProvider):
    """Plan one tool call (tool_name + args), then answer on the next turn."""

    def __init__(self, tool_name: str, args: dict[str, Any]) -> None:
        super().__init__(response_text="done")
        self._calls = 0
        self._tool_name = tool_name
        self._args = args

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="plan-tool",
                model="test",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(tool_name=self._tool_name, args=self._args).model_dump(
                            mode="json"
                        )
                    ]
                },
            )
        return await super().complete(request)


def _fs_capabilities() -> Any:
    return schema.ClientCapabilities(
        fs=schema.FileSystemCapabilities(read_text_file=True, write_text_file=True)
    )


@pytest.mark.asyncio
async def test_file_write_routes_through_client_fs(tmp_path: Any) -> None:
    target = tmp_path / "out.txt"
    agent = create_agent(
        provider=_PlanToolThenFinish(
            "file_write", {"path": str(target), "content": "FROM AGENT"}
        ),
        tools=ToolSet.only("file_write"),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    server.on_connect(client)
    await server.initialize(protocol_version=1, client_capabilities=_fs_capabilities())
    session = await server.new_session(cwd=str(tmp_path))

    resp = await server.prompt(
        prompt=[_text_block("write it")], session_id=session.session_id
    )

    assert resp.stop_reason == "end_turn"
    # Routed to the editor, not local disk.
    assert client.fs_writes == [(str(target), "FROM AGENT")]
    assert not target.exists()


@pytest.mark.asyncio
async def test_file_read_routes_through_client_fs(tmp_path: Any) -> None:
    target = tmp_path / "src.txt"
    target.write_text("ON DISK", encoding="utf-8")  # exists for path validation
    agent = create_agent(
        provider=_PlanToolThenFinish("read_file", {"path": str(target)}),
        tools=ToolSet.only("read_file"),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    client.read_content = "UNSAVED EDITOR CONTENT"
    server.on_connect(client)
    await server.initialize(protocol_version=1, client_capabilities=_fs_capabilities())
    session = await server.new_session(cwd=str(tmp_path))

    await server.prompt(prompt=[_text_block("read it")], session_id=session.session_id)

    # The read went through the client and returned the editor's (unsaved) view.
    assert client.fs_reads == [str(target)]


@pytest.mark.asyncio
async def test_no_fs_capability_uses_local_disk(tmp_path: Any) -> None:
    target = tmp_path / "out.txt"
    agent = create_agent(
        provider=_PlanToolThenFinish(
            "file_write", {"path": str(target), "content": "ON DISK"}
        ),
        tools=ToolSet.only("file_write"),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    server.on_connect(client)
    # initialize WITHOUT fs capabilities.
    await server.initialize(protocol_version=1)
    session = await server.new_session(cwd=str(tmp_path))

    await server.prompt(prompt=[_text_block("write it")], session_id=session.session_id)

    # No client fs calls; the write hit local disk.
    assert client.fs_writes == []
    assert target.read_text(encoding="utf-8") == "ON DISK"


@pytest.mark.asyncio
async def test_bash_routes_through_client_terminal(tmp_path: Any) -> None:
    agent = create_agent(
        provider=_PlanToolThenFinish("bash", {"command": "echo hi"}),
        tools=ToolSet.only("bash"),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    client.terminal_output_text = "hi\n"
    server.on_connect(client)
    await server.initialize(
        protocol_version=1,
        client_capabilities=schema.ClientCapabilities(terminal=True),
    )
    session = await server.new_session(cwd=str(tmp_path))

    resp = await server.prompt(
        prompt=[_text_block("run echo")], session_id=session.session_id
    )

    assert resp.stop_reason == "end_turn"
    # Full editor-terminal lifecycle, in order.
    assert client.terminal_events == [
        ("create", "echo hi"),
        ("wait", "term-1"),
        ("output", "term-1"),
        ("release", "term-1"),
    ]


@pytest.mark.asyncio
async def test_no_terminal_capability_uses_local_subprocess(tmp_path: Any) -> None:
    agent = create_agent(
        provider=_PlanToolThenFinish("bash", {"command": "echo hi"}),
        tools=ToolSet.only("bash"),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    server.on_connect(client)
    await server.initialize(protocol_version=1)  # no terminal capability
    session = await server.new_session(cwd=str(tmp_path))

    await server.prompt(prompt=[_text_block("run echo")], session_id=session.session_id)

    # Ran locally — the client terminal was never touched.
    assert client.terminal_events == []


def _update_kinds(client: FakeAcpClient) -> list[str]:
    return [type(u).__name__ for u in client.updates]


@pytest.mark.asyncio
async def test_new_session_emits_available_commands() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    await _bind(server, client)
    assert "AvailableCommandsUpdate" in _update_kinds(client)


@pytest.mark.asyncio
async def test_set_session_mode_emits_current_mode_update() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    session_id = await _bind(server, client)
    client.updates.clear()

    await server.set_session_mode(session_id=session_id, mode_id="strict")

    modes = [u for u in client.updates if type(u).__name__ == "CurrentModeUpdate"]
    assert len(modes) == 1
    assert modes[0].current_mode_id == "strict"


@pytest.mark.asyncio
async def test_slash_clear_clears_transcript_without_running() -> None:
    provider = FakeProvider(response_text="should-not-run")
    server = AgentAcpServer(create_agent(provider=provider, tools=ToolSet.only()))
    client = FakeAcpClient()
    session_id = await _bind(server, client)
    await server.prompt(prompt=[_text_block("remember this")], session_id=session_id)
    assert server._sessions[session_id].transcript  # non-empty
    client.updates.clear()

    resp = await server.prompt(prompt=[_text_block("/clear")], session_id=session_id)

    assert resp.stop_reason == "end_turn"
    assert server._sessions[session_id].transcript == []
    assert "cleared" in client.message_text().lower()


@pytest.mark.asyncio
async def test_slash_help_lists_commands() -> None:
    server = AgentAcpServer(
        create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    )
    client = FakeAcpClient()
    session_id = await _bind(server, client)
    client.updates.clear()

    resp = await server.prompt(prompt=[_text_block("/help")], session_id=session_id)

    assert resp.stop_reason == "end_turn"
    text = client.message_text()
    assert "/clear" in text and "/help" in text


@pytest.mark.asyncio
async def test_resume_session_returns_modes_without_replay() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="hi"), tools=ToolSet.only()
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    session_id = await _bind(server, client)
    await server.prompt(prompt=[_text_block("hi")], session_id=session_id)
    client.updates.clear()

    resp = await server.resume_session(session_id=session_id, cwd="/tmp")
    # resume does NOT replay history.
    assert client.updates == []
    assert resp.modes.current_mode_id == "default"


@pytest.mark.asyncio
async def test_prompt_streams_text_and_ends_turn() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="Hello there friend"),
        tools=ToolSet.only(),
    )
    server = AgentAcpServer(agent)
    client = FakeAcpClient()
    session_id = await _bind(server, client)

    resp = await server.prompt(prompt=[_text_block("hi")], session_id=session_id)

    assert resp.stop_reason == "end_turn"
    assert client.message_text() == "Hello there friend"
    assert client.permission_requests == []


@pytest.mark.asyncio
async def test_prompt_permission_reject_is_refusal() -> None:
    server = AgentAcpServer(_gated_agent())
    client = FakeAcpClient(allow_option_id=None)  # deny
    session_id = await _bind(server, client)

    resp = await server.prompt(prompt=[_text_block("run echo")], session_id=session_id)

    assert len(client.permission_requests) == 1
    # A rejected approval is a failed run, not a normal turn end. ACP has no
    # error stop reason, so the adapter surfaces it as a refusal.
    assert resp.stop_reason == "refusal"
    # The tool was rejected, so the model never produced its follow-up answer.
    assert "all done" not in client.message_text()


@pytest.mark.asyncio
async def test_prompt_permission_approve_runs_once_and_finishes() -> None:
    server = AgentAcpServer(_gated_agent())
    client = FakeAcpClient(allow_option_id=ResumeAction.APPROVE.value)
    session_id = await _bind(server, client)

    resp = await server.prompt(prompt=[_text_block("run echo")], session_id=session_id)

    # Exactly one approval round-trip: the gate must NOT re-ask the
    # already-approved call (regression guard against an approve/ask loop).
    assert len(client.permission_requests) == 1
    option_ids = {o.option_id for o in client.permission_requests[0]["options"]}
    assert ResumeAction.APPROVE.value in option_ids
    assert resp.stop_reason == "end_turn"
    assert "all done" in client.message_text()


@pytest.mark.asyncio
async def test_cancel_during_permission_stops_turn() -> None:
    server = AgentAcpServer(_gated_agent())

    async def _cancel(session_id: str) -> None:
        await server.cancel(session_id)

    # Deny + cancel arrives while the approval is pending.
    client = FakeAcpClient(allow_option_id=None, on_permission=_cancel)
    session_id = await _bind(server, client)

    resp = await server.prompt(prompt=[_text_block("run echo")], session_id=session_id)
    assert len(client.permission_requests) == 1
    assert resp.stop_reason == "cancelled"
    # The run was not resumed past the cancelled approval.
    assert "all done" not in client.message_text()


def test_tool_kind_and_text_update_mapping() -> None:
    from agent_driver.adapters.acp.mapping import text_update_for, tool_kind_for
    from agent_driver.contracts.enums.runtime import RuntimeEventType
    from agent_driver.contracts.stream import RunStreamEvent

    assert tool_kind_for("read_file") == "read"
    assert tool_kind_for("bash") == "execute"
    assert tool_kind_for("totally_unknown") == "other"

    token = RunStreamEvent(
        stream_id="s:1",
        run_id="s",
        attempt_id="s:1",
        seq=1,
        event=RuntimeEventType.TOKEN_DELTA.value,
        data={"delta_text": "hi"},
    )
    update = text_update_for(token)
    assert type(update).__name__ == "AgentMessageChunk"
    assert update.content.text == "hi"

    noise = RunStreamEvent(
        stream_id="s:2",
        run_id="s",
        attempt_id="s:2",
        seq=2,
        event="checkpoint_saved",
        data={},
    )
    assert text_update_for(noise) is None


def _text_block(text: str) -> Any:
    """Construct an ACP text content block across minor schema variants."""
    from acp import text_block

    return text_block(text)
