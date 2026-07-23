"""Epic 026: request_only_context — model-visible, never durable dialogue."""

from __future__ import annotations

import pytest

from agent_driver.contracts import AgentRunInput
from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent

_MARKER = "RAG-БЛОК-ЭФЕМЕРНЫЙ-826"


class _CapturingProvider(FakeProvider):
    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[list[dict]] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(
            [
                {"role": str(m.role), "content": m.content or ""}
                for m in request.messages
            ]
        )
        return await super().complete(request)


def _run_input(run_id: str, *, request_only: list[ChatMessage]) -> AgentRunInput:
    return AgentRunInput(
        messages=[
            ChatMessage(role=ChatRole.USER, content="перечисли встречи"),
            ChatMessage(role=ChatRole.ASSISTANT, content="1. Альфа\n2. Бета"),
            ChatMessage(role=ChatRole.USER, content="сколько из этих в июле?"),
        ],
        request_only_context=request_only,
        run_id=run_id,
        thread_id="t-roc",
        agent_id="agent",
        graph_preset="single_react",
    )


@pytest.mark.asyncio
async def test_request_only_context_visible_before_latest_user_turn() -> None:
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        _run_input(
            "r-roc1",
            request_only=[
                ChatMessage(role=ChatRole.USER, content=f"Справочный контекст: {_MARKER}")
            ],
        )
    )
    assert output.status.value == "completed"
    sent = provider.requests[0]
    contents = [m["content"] for m in sent]
    marker_idx = next(i for i, c in enumerate(contents) if _MARKER in c)
    question_idx = next(i for i, c in enumerate(contents) if "сколько из этих" in c)
    assert marker_idx == question_idx - 1  # framing right before the question
    # Dialogue history is intact and precedes the ephemeral block.
    assert any("1. Альфа" in c for c in contents[:marker_idx])


@pytest.mark.asyncio
async def test_request_only_context_never_enters_durable_transcript() -> None:
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(
        _run_input(
            "r-roc2",
            request_only=[ChatMessage(role=ChatRole.USER, content=_MARKER)],
        )
    )
    # Neither the run's message transcript nor its checkpoint may carry the block.
    assert all(_MARKER not in (m.content or "") for m in output.messages)
    checkpoint = output.checkpoint
    if checkpoint is not None:
        dumped = checkpoint.model_dump_json()
        # run_input echoes its own field; the PROTOCOL transcript must not.
        protocol = (checkpoint.metadata or {}).get("protocol_messages") or []
        assert all(
            _MARKER not in str(item.get("content", "")) for item in protocol
        ), dumped[:500]


@pytest.mark.asyncio
async def test_empty_request_only_context_is_inert() -> None:
    provider = _CapturingProvider()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    output = await agent.run(_run_input("r-roc3", request_only=[]))
    assert output.status.value == "completed"
    assert provider.requests  # request built normally
