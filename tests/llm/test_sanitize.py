"""E8: surrogate/NUL sanitization before provider calls."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts import AgentRunInput
from agent_driver.llm import sanitize_request_messages, strip_surrogates
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.sdk import ToolSet, create_agent


def test_strip_lone_surrogates_and_nul() -> None:
    dirty = "ok\ud800mid\x00end"
    clean = strip_surrogates(dirty)
    assert clean == "okmidend"
    # The result encodes to UTF-8 (the original would have raised).
    clean.encode("utf-8")
    with pytest.raises(UnicodeEncodeError):
        dirty.encode("utf-8")


def test_clean_text_returned_identity() -> None:
    text = "Café — 日本語 — 🚀"  # legitimate Unicode preserved
    assert strip_surrogates(text) is text
    assert text.encode("utf-8")  # encodes fine


def test_sanitize_messages_only_copies_dirty() -> None:
    clean_msg = ChatMessage(role=ChatRole.USER, content="hello 🌍")
    dirty_msg = ChatMessage(role=ChatRole.USER, content="bad\ud800here")
    out = sanitize_request_messages([clean_msg, dirty_msg])
    assert out[0] is clean_msg  # untouched message kept by identity
    assert out[1].content == "badhere"


@pytest.mark.asyncio
async def test_surrogate_content_sanitized_before_provider() -> None:
    """A run whose input carries a lone surrogate reaches the provider clean."""

    class _Capturing(FakeProvider):
        def __init__(self) -> None:
            super().__init__(response_text="ok")
            self.user_text = ""

        async def complete(self, request: LlmRequest) -> LlmResponse:
            self.user_text = " ".join(
                m.content for m in request.messages if m.role == "user"
            )
            # Must not raise — proves the content is UTF-8 encodable.
            self.user_text.encode("utf-8")
            return await super().complete(request)

    provider = _Capturing()
    agent = create_agent(provider=provider, tools=ToolSet.only())
    await agent.run(
        AgentRunInput(
            input="payload\ud800tail",
            run_id="r1",
            agent_id="a",
            thread_id="t",
            graph_preset="single_react",
        )
    )
    assert "\ud800" not in provider.user_text
    assert "payloadtail" in provider.user_text
