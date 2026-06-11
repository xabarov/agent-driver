"""Tests for ``fork_subagent`` — system-prompt-cached subagent spawn.

Coverage:
- Parent system prompt is injected as the child's first message
- Caller-supplied ``spec.system_prompt`` is overridden by the fork
- All other spec fields pass through unchanged (allowlist, tool_choice, …)
- abort_handle inheritance behaves the same as ``run_subagent``
"""

from __future__ import annotations

import pytest

from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.runtime.abort import RunAbortHandle
from agent_driver.sdk import create_agent
from agent_driver.sdk.fork import fork_subagent
from agent_driver.sdk.subagent import SubagentSpec
from agent_driver.tools import ToolSet


class _CapturingProvider(FakeProvider):
    """Records every LlmRequest so the test can assert on messages."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return LlmResponse(
            message=ChatMessage(role="assistant", content="ok"),
            finish_reason=LlmFinishReason.STOP,
            usage=UsageSummary(model_provider="fake", model_name="test"),
            provider="fake",
            model="test",
            metadata={},
        )


@pytest.mark.asyncio
async def test_fork_injects_parent_system_prompt_byte_identical() -> None:
    """The parent's exact rendered system prompt becomes the child's
    first message — character-for-character. This is the contract that
    enables cache reuse."""
    provider = _CapturingProvider()
    parent = create_agent(provider=provider, tools=ToolSet.only())
    parent_prompt = (
        "You are excel_ai's chart specialist. Build VegaLite specs."
    )
    spec = SubagentSpec(agent_type="chart_render", prompt="render the chart")
    await fork_subagent(parent, parent_prompt, spec)
    request = provider.requests[0]
    system_msgs = [m for m in request.messages if m.role.value == "system"]
    assert any(m.content == parent_prompt for m in system_msgs)


@pytest.mark.asyncio
async def test_fork_overrides_spec_system_prompt() -> None:
    """If the spec had its own system_prompt, the fork wins. Callers
    that want extra instructions should bake them into the parent
    prompt or the user message — fork is "exact cache reuse"."""
    provider = _CapturingProvider()
    parent = create_agent(provider=provider, tools=ToolSet.only())
    parent_prompt = "PARENT SYSTEM"
    spec = SubagentSpec(
        agent_type="c",
        prompt="x",
        system_prompt="WAS_SPEC_SYSTEM",  # will be overridden
    )
    await fork_subagent(parent, parent_prompt, spec)
    request = provider.requests[0]
    system_msgs = [m for m in request.messages if m.role.value == "system"]
    assert any(m.content == parent_prompt for m in system_msgs)
    assert not any("WAS_SPEC_SYSTEM" in m.content for m in system_msgs)


@pytest.mark.asyncio
async def test_fork_preserves_spec_allowlist_and_tool_choice() -> None:
    """Other spec fields pass through unchanged — only system_prompt
    is overridden by the fork."""
    provider = _CapturingProvider()
    parent = create_agent(provider=provider, tools=ToolSet.only("web_search"))
    parent_prompt = "be nice"
    spec = SubagentSpec(
        agent_type="search",
        prompt="find it",
        allowed_tools=("web_search",),
        tool_choice="required",
        max_tool_calls=1,
    )
    await fork_subagent(parent, parent_prompt, spec)
    request = provider.requests[0]
    # tool_choice flows through
    assert request.tool_choice == "required"
    # allowlist → only the allowed tool surfaces in the schema
    tool_names = [t["function"]["name"] for t in request.tools]
    assert tool_names == ["web_search"]


@pytest.mark.asyncio
async def test_fork_aborted_parent_handle_cascades_to_child() -> None:
    """Same abort semantics as run_subagent — pre-aborted parent →
    child cancelled, provider never called."""
    provider = _CapturingProvider()
    parent = create_agent(provider=provider, tools=ToolSet.only())
    handle = RunAbortHandle()
    handle.abort("user_cancel")
    spec = SubagentSpec(agent_type="cancelled", prompt="x")
    result = await fork_subagent(
        parent, "system", spec, parent_abort_handle=handle
    )
    from agent_driver.contracts.enums import RunStatus

    assert result.status == RunStatus.CANCELLED
    assert provider.requests == []
