"""Tests for the headless session/approval gateway."""

from __future__ import annotations

import json

import pytest

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.gateway import (
    AgentGateway,
    GatewayError,
    GatewayEvent,
    GatewayEventKind,
)
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
)
from agent_driver.sdk import ToolSet, create_agent


def test_event_to_sse_frame() -> None:
    event = GatewayEvent(
        kind=GatewayEventKind.COMPLETED,
        session_id="s1",
        run_id="r1",
        seq=3,
        data={"answer": "hi"},
    )
    frame = event.to_sse()
    assert frame.startswith("id: 3\nevent: completed\ndata: ")
    assert frame.endswith("\n\n")
    payload = json.loads(frame.split("data: ", 1)[1].strip())
    assert payload["answer"] == "hi"
    assert payload["run_id"] == "r1"


@pytest.mark.asyncio
async def test_submit_completes() -> None:
    agent = create_agent(
        provider=FakeProvider(response_text="done"), tools=ToolSet.only()
    )
    gateway = AgentGateway(agent)
    events = [e async for e in gateway.submit("s1", "hello", run_id="r1")]
    kinds = [e.kind for e in events]
    assert kinds == [GatewayEventKind.STARTED, GatewayEventKind.COMPLETED]
    assert events[-1].data["answer"] == "done"
    assert gateway.pending("s1") == []


class _BashThenFinish(FakeProvider):
    """Plan a bash call on the first turn, then answer on the next."""

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
                            args={"command": "sudo apt-get install nginx"},
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


@pytest.mark.asyncio
async def test_action_required_then_respond_resumes() -> None:
    agent = create_agent(provider=_BashThenFinish(), tools=ToolSet.only("bash"))
    gateway = AgentGateway(
        agent,
        tool_gate=build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD)),
    )

    first = [e async for e in gateway.submit("s1", "install nginx", run_id="r1")]
    assert [e.kind for e in first] == [
        GatewayEventKind.STARTED,
        GatewayEventKind.ACTION_REQUIRED,
    ]
    action_event = first[-1]
    assert "approve" in action_event.data["allowed_actions"]
    assert gateway.pending("s1") == ["r1"]

    # Operator rejects the risky command; the run resumes and finishes.
    second = [
        e async for e in gateway.respond("s1", run_id="r1", action=ResumeAction.REJECT)
    ]
    assert second[-1].kind in (GatewayEventKind.COMPLETED, GatewayEventKind.FAILED)
    assert gateway.pending("s1") == []


@pytest.mark.asyncio
async def test_respond_without_pending_raises() -> None:
    agent = create_agent(provider=FakeProvider(response_text="x"), tools=ToolSet.only())
    gateway = AgentGateway(agent)
    with pytest.raises(GatewayError, match="no pending approval"):
        _ = [
            e
            async for e in gateway.respond(
                "s1", run_id="nope", action=ResumeAction.APPROVE
            )
        ]
