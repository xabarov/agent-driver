"""Gateway: a session turn pauses for approval, then resumes.

The headless gateway routes turns by session and correlates the approval
round-trip: a risky tool call surfaces an ``action_required`` event; the
operator's ``respond`` resumes the parked run.

    python examples/cookbook/06_gateway.py
"""

from __future__ import annotations

import asyncio

from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.gateway import AgentGateway
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.permissions import (
    PermissionMode,
    PermissionPolicy,
    build_permission_gate,
)
from agent_driver.sdk import ToolSet, create_agent


class _BashThenFinish(FakeProvider):
    """Plan a risky bash call first, then answer once it is resolved."""

    def __init__(self) -> None:
        super().__init__(response_text="all set")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="demo",
                model="demo",
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


async def main() -> None:
    agent = create_agent(provider=_BashThenFinish(), tools=ToolSet.only("bash"))
    gateway = AgentGateway(
        agent,
        tool_gate=build_permission_gate(PermissionPolicy(mode=PermissionMode.STANDARD)),
    )

    async for event in gateway.submit("session-1", "install nginx", run_id="g1"):
        print("submit ->", event.kind.value, event.data.get("reason", ""))

    assert gateway.pending("session-1") == ["g1"]
    print("pending:", gateway.pending("session-1"))

    async for event in gateway.respond(
        "session-1", run_id="g1", action=ResumeAction.REJECT
    ):
        print("respond ->", event.kind.value, event.data.get("answer", ""))
    assert not gateway.pending("session-1")


if __name__ == "__main__":
    asyncio.run(main())
