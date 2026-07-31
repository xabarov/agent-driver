"""Epic 044 B: a degenerate select-context replacement falls open to the prior request."""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.runtime.lifecycle_hooks import (
    BaseRunLifecycleHook,
    dispatch_before_llm,
)


def _req(*contents: tuple[str, str]) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=role, content=content) for role, content in contents],
        model="m",
    )


class _ReturnsHook(BaseRunLifecycleHook):
    name = "returns"

    def __init__(self, replacement) -> None:
        self._replacement = replacement

    async def before_llm_request(self, context, request):  # noqa: ANN001
        return self._replacement


@pytest.mark.asyncio
async def test_empty_messages_replacement_is_rejected() -> None:
    original = _req(("system", "sys"), ("user", "real question"))
    hook = _ReturnsHook(_req())  # select_context filtered EVERYTHING out
    out = await dispatch_before_llm([hook], None, original)
    assert out is original  # fell open, prompt not blanked


@pytest.mark.asyncio
async def test_system_only_replacement_is_rejected() -> None:
    # The all([]) is True trap: only a system message survived → no real turn.
    original = _req(("system", "sys"), ("user", "real question"))
    hook = _ReturnsHook(_req(("system", "sys")))
    out = await dispatch_before_llm([hook], None, original)
    assert out is original


@pytest.mark.asyncio
async def test_non_request_replacement_is_rejected() -> None:
    original = _req(("user", "q"))
    hook = _ReturnsHook(object())  # not shaped like a request
    out = await dispatch_before_llm([hook], None, original)
    assert out is original


@pytest.mark.asyncio
async def test_valid_replacement_is_applied() -> None:
    original = _req(("user", "q"))
    replacement = _req(("system", "sys"), ("user", "retrieved context + q"))
    hook = _ReturnsHook(replacement)
    out = await dispatch_before_llm([hook], None, original)
    assert out is replacement  # a real select_context still works


@pytest.mark.asyncio
async def test_raising_hook_still_falls_open() -> None:
    class _Boom(BaseRunLifecycleHook):
        name = "boom"

        async def before_llm_request(self, context, request):  # noqa: ANN001
            raise RuntimeError("boom")

    original = _req(("user", "q"))
    out = await dispatch_before_llm([_Boom()], None, original)
    assert out is original
