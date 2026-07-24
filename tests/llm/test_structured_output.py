"""Epic 036: structured output via the forced tool-call channel."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.llm.structured import StructuredOutputError, structured_completion

_SCHEMA = {
    "type": "object",
    "properties": {"title": {"type": "string"}, "count": {"type": "integer"}},
    "required": ["title"],
}


class _ScriptedProvider(FakeProvider):
    """Returns queued planned_tool_calls (one per complete call)."""

    def __init__(self, scripted: list) -> None:
        super().__init__(response_text="")
        self._scripted = list(scripted)
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        planned = self._scripted.pop(0) if self._scripted else None
        response = await super().complete(request)
        meta = dict(response.message.metadata or {})
        if planned is not None:
            meta["planned_tool_calls"] = planned
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


def _msgs() -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content="дай результат")]


class _RealShapeProvider(FakeProvider):
    """Mirrors the REAL OpenAI-compatible provider shape (regression guard).

    ``normalize_openai_completion_payload`` puts parsed tool calls on the
    RESPONSE metadata (``response.metadata['planned_tool_calls']``), each keyed
    by ``tool_name`` (not ``message.metadata`` / ``name``). The first cut of
    ``_extract_emit_args`` read only ``message.metadata['...']['name']`` → it
    matched FakeProvider-shaped tests but NEVER a live provider (sonnet/deepseek/
    gemini all returned valid tool calls that structured_completion ignored).
    """

    async def complete(self, request: LlmRequest) -> LlmResponse:
        response = await super().complete(request)
        resp_meta = dict(response.metadata or {})
        resp_meta["planned_tool_calls"] = [
            {
                "tool_name": "emit_result",
                "args": {"title": "Живой", "count": 7},
                "tool_call_id": "toolu_abc",
                "metadata": {"provider_tool_call_index": 0},
            }
        ]
        return response.model_copy(update={"metadata": resp_meta})


@pytest.mark.asyncio
async def test_reads_real_provider_response_metadata_shape() -> None:
    result = await structured_completion(
        provider=_RealShapeProvider(response_text=""), messages=_msgs(), schema=_SCHEMA
    )
    assert result == {"title": "Живой", "count": 7}


@pytest.mark.asyncio
async def test_valid_emit_first_try() -> None:
    provider = _ScriptedProvider(
        [[{"name": "emit_result", "args": {"title": "Отчёт", "count": 3}}]]
    )
    result = await structured_completion(
        provider=provider, messages=_msgs(), schema=_SCHEMA
    )
    assert result == {"title": "Отчёт", "count": 3}
    # The request forced the emit tool.
    assert provider.requests[0].tool_choice["function"]["name"] == "emit_result"
    assert provider.requests[0].tools[0]["function"]["parameters"] == _SCHEMA


@pytest.mark.asyncio
async def test_prose_then_valid_retry() -> None:
    # deepseek-class flake: no tool call first (prose), valid emit on the nudge.
    provider = _ScriptedProvider(
        [
            None,
            [{"name": "emit_result", "args": {"title": "Восстановлено"}}],
        ]
    )
    result = await structured_completion(
        provider=provider, messages=_msgs(), schema=_SCHEMA
    )
    assert result == {"title": "Восстановлено"}
    assert len(provider.requests) == 2
    # The corrective turn was appended.
    assert any("схем" in (m.content or "") for m in provider.requests[1].messages)


@pytest.mark.asyncio
async def test_missing_required_field_retries_then_raises() -> None:
    provider = _ScriptedProvider(
        [
            [{"name": "emit_result", "args": {"count": 1}}],  # missing 'title'
            [{"name": "emit_result", "args": {"count": 2}}],  # still missing
        ]
    )
    with pytest.raises(StructuredOutputError) as exc:
        await structured_completion(
            provider=provider, messages=_msgs(), schema=_SCHEMA, max_retries=1
        )
    assert "title" in str(exc.value)


@pytest.mark.asyncio
async def test_type_violation_is_rejected() -> None:
    provider = _ScriptedProvider(
        [
            [
                {"name": "emit_result", "args": {"title": "X", "count": "три"}}
            ],  # count not int
            [{"name": "emit_result", "args": {"title": "X", "count": 3}}],
        ]
    )
    result = await structured_completion(
        provider=provider, messages=_msgs(), schema=_SCHEMA, max_retries=1
    )
    assert result == {"title": "X", "count": 3}


@pytest.mark.asyncio
async def test_never_calls_tool_raises_not_silent() -> None:
    provider = _ScriptedProvider([None, None])
    with pytest.raises(StructuredOutputError):
        await structured_completion(
            provider=provider, messages=_msgs(), schema=_SCHEMA, max_retries=1
        )


# --- Run-level AgentRunInput.structured_output (phase A/C) ---------------------


@pytest.mark.asyncio
async def test_run_level_structured_output_valid_and_invalid() -> None:
    from agent_driver.contracts import AgentRunInput
    from agent_driver.sdk import ToolSet, create_agent

    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }

    def _run_input(run_id: str) -> AgentRunInput:
        return AgentRunInput(
            input="q",
            run_id=run_id,
            thread_id="t",
            agent_id="agent",
            graph_preset="single_react",
            structured_output=schema,
        )

    # Valid JSON terminal answer → parsed object stored, no error.
    valid = await create_agent(
        provider=FakeProvider(response_text='{"answer": "готово"}'),
        tools=ToolSet.only(),
    ).run(_run_input("r-so-ok"))
    md = valid.metadata or {}
    assert md.get("structured_output") == {"answer": "готово"}
    assert "structured_output_error" not in md

    # Prose terminal answer → error signal, NOT a silent completed-with-junk.
    invalid = await create_agent(
        provider=FakeProvider(response_text="просто текст без структуры"),
        tools=ToolSet.only(),
    ).run(_run_input("r-so-bad"))
    md2 = invalid.metadata or {}
    assert "structured_output" not in md2
    assert md2.get("structured_output_error")


@pytest.mark.asyncio
async def test_structured_output_inert_when_unset() -> None:
    from agent_driver.contracts import AgentRunInput
    from agent_driver.sdk import ToolSet, create_agent

    out = await create_agent(
        provider=FakeProvider(response_text="обычная проза"), tools=ToolSet.only()
    ).run(
        AgentRunInput(
            input="q",
            run_id="r-so-none",
            thread_id="t",
            agent_id="agent",
            graph_preset="single_react",
        )
    )
    md = out.metadata or {}
    assert "structured_output" not in md and "structured_output_error" not in md


# --- reasoning disable for thinking-mode compat (Qwen3) ------------------------


@pytest.mark.asyncio
async def test_plain_call_first_never_disables_reasoning_when_it_succeeds() -> None:
    """Reasoning-mandatory models (kimi-k2-thinking) must NOT get reasoning off.

    The plain forced call succeeds → reasoning stays untouched (None). Sending
    ``reasoning={enabled:false}`` to a reasoning-mandatory model is a hard 400.
    """
    provider = _ScriptedProvider([[{"name": "emit_result", "args": {"title": "X"}}]])
    result = await structured_completion(
        provider=provider, messages=_msgs(), schema=_SCHEMA
    )
    assert result == {"title": "X"}
    assert len(provider.requests) == 1
    assert provider.requests[0].reasoning is None


class _ThinkingRejectProvider(FakeProvider):
    """Qwen3-thinking: the plain forced tool_choice 400s; only reasoning-off works."""

    def __init__(self) -> None:
        super().__init__(response_text="")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        if request.reasoning is None:
            raise RuntimeError("tool_choice does not support ... in thinking mode")
        response = await super().complete(request)
        meta = dict(response.message.metadata or {})
        meta["planned_tool_calls"] = [{"name": "emit_result", "args": {"title": "OK"}}]
        return response.model_copy(
            update={"message": response.message.model_copy(update={"metadata": meta})}
        )


@pytest.mark.asyncio
async def test_reasoning_disabled_as_fallback_on_plain_failure() -> None:
    provider = _ThinkingRejectProvider()
    result = await structured_completion(
        provider=provider, messages=_msgs(), schema=_SCHEMA
    )
    assert result == {"title": "OK"}
    # Plain first (None) → raised → retried with reasoning disabled.
    assert [r.reasoning for r in provider.requests] == [None, {"enabled": False}]


@pytest.mark.asyncio
async def test_opt_out_reraises_plain_failure_without_reasoning_fallback() -> None:
    provider = _ThinkingRejectProvider()
    with pytest.raises(RuntimeError):
        await structured_completion(
            provider=provider, messages=_msgs(), schema=_SCHEMA, disable_reasoning=False
        )
    # No reasoning fallback attempted.
    assert all(r.reasoning is None for r in provider.requests)


def test_payload_builder_forwards_reasoning_only_when_set() -> None:
    from agent_driver.llm.providers_impl.openai_compatible.payload import (
        build_openai_completion_payload,
    )

    def _payload(req: LlmRequest) -> dict:
        return build_openai_completion_payload(
            req, model="m", max_tokens_default=None, extra_body={}, stream=False
        )

    msgs = [ChatMessage(role=ChatRole.USER, content="hi")]
    with_reasoning = _payload(LlmRequest(messages=msgs, reasoning={"enabled": False}))
    assert with_reasoning["reasoning"] == {"enabled": False}
    without = _payload(LlmRequest(messages=msgs))
    assert "reasoning" not in without
