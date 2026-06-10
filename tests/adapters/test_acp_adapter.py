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

    async def session_update(self, *, session_id: str, update: Any, **_: Any) -> None:
        self.updates.append(update)

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
