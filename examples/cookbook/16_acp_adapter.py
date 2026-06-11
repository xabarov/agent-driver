"""Serve an agent over the Agent Client Protocol (ACP), offline.

Phase 1 platform adapter: ``AgentAcpServer`` exposes any agent to ACP clients
(Zed and other editors) over stdio. In production you run it via
``agent-driver acp`` (or :func:`agent_driver.adapters.acp.serve_acp`), which
speaks JSON-RPC on stdin/stdout. Here we drive the same server in-process with a
tiny fake ACP client so the round-trip — capabilities, a streamed answer, and a
tool-approval interrupt mapped to ``request_permission`` — runs with no editor
and no network.

    python examples/cookbook/16_acp_adapter.py

Requires the optional dependency: ``pip install 'agent-driver[acp]'``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from acp import schema, text_block

from agent_driver.adapters.acp import AgentAcpServer
from agent_driver.contracts.enums import ResumeAction
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.tools import ToolCall
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.tool_gate import ToolGateAsk, ToolGateContext
from agent_driver.sdk import ToolSet, create_agent


class PlanThenAnswer(FakeProvider):
    """Plan a (safe) bash call on turn 1, then answer on the next turn."""

    def __init__(self) -> None:
        super().__init__(response_text="All set — the workspace is ready.")
        self._calls = 0

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self._calls += 1
        if self._calls == 1:
            return LlmResponse(
                message=ChatMessage(role="assistant", content=""),
                finish_reason=LlmFinishReason.TOOL_CALLS,
                provider="plan-then-answer",
                model="demo",
                metadata={
                    "planned_tool_calls": [
                        ToolCall(
                            tool_name="bash", args={"command": "echo ready"}
                        ).model_dump(mode="json")
                    ]
                },
            )
        return await super().complete(request)


class DemoAcpClient:
    """Records session updates; approves the first permission request."""

    def __init__(self) -> None:
        self.updates: list[Any] = []

    async def session_update(self, *, session_id: str, update: Any, **_: Any) -> None:
        self.updates.append(update)

    async def request_permission(
        self, *, options: list[Any], session_id: str, tool_call: Any, **_: Any
    ) -> schema.RequestPermissionResponse:
        print(f"  [client] approval requested for {tool_call.title!r}")
        return schema.RequestPermissionResponse(
            outcome=schema.AllowedOutcome(
                option_id=ResumeAction.APPROVE.value, outcome="selected"
            )
        )

    def answer_text(self) -> str:
        return "".join(
            u.content.text
            for u in self.updates
            if type(u).__name__ == "AgentMessageChunk"
        )


async def _ask_gate(_ctx: ToolGateContext) -> ToolGateAsk:
    """Pause every tool call for operator approval (forces the ACP round-trip)."""
    return ToolGateAsk(message="Approve this tool call?")


async def main() -> None:
    agent = create_agent(
        provider=PlanThenAnswer(),
        tools=ToolSet.only("bash"),
        tool_gate=_ask_gate,
    )
    server = AgentAcpServer(agent, name="agent-driver-demo", version="1.0.0")
    client = DemoAcpClient()
    server.on_connect(client)

    init = await server.initialize(protocol_version=1)
    print("initialize ->", init.agent_info.name, init.agent_info.version)

    session = await server.new_session(cwd="/tmp")
    print("new_session ->", session.session_id)

    resp = await server.prompt(
        prompt=[text_block("Set up the workspace")], session_id=session.session_id
    )
    print("prompt stop_reason ->", resp.stop_reason)
    print("streamed answer    ->", client.answer_text() or "(none)")


if __name__ == "__main__":
    asyncio.run(main())
