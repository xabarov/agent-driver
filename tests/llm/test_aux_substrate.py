"""Epic 034: cache-safe aux-call substrate."""

from __future__ import annotations

import pytest

from agent_driver.contracts.enums import ChatRole
from agent_driver.contracts.messages import ChatMessage
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.aux import (
    AuxCachePrefix,
    aux_completion,
    aux_fork_event_payload,
    merge_aux_usage,
)
from agent_driver.llm.contracts import LlmRequest, LlmResponse
from agent_driver.llm.providers_impl.fake import FakeProvider
from agent_driver.observability.cost_ledger import CostLedger


class _CapturingProvider(FakeProvider):
    """Records the request the substrate built."""

    def __init__(self) -> None:
        super().__init__(response_text="ok")
        self.requests: list[LlmRequest] = []

    async def complete(self, request: LlmRequest) -> LlmResponse:
        self.requests.append(request)
        return await super().complete(request)


def _msgs(text: str = "запрос") -> list[ChatMessage]:
    return [ChatMessage(role=ChatRole.USER, content=text)]


@pytest.mark.asyncio
async def test_aux_completion_merges_usage_into_ledger() -> None:
    provider = _CapturingProvider()
    ledger = CostLedger()
    await aux_completion(
        provider=provider,
        messages=_msgs(),
        model="m1",
        task="compaction",
        cost_ledger=ledger,
    )
    assert "m1" in ledger.per_model
    tally = ledger.per_model["m1"]
    assert tally.api_calls == 1 and tally.input_tokens > 0
    # The built request carries the task tag and no cache by default.
    req = provider.requests[0]
    assert req.metadata["aux_task"] == "compaction"
    assert req.enable_prompt_cache is False


@pytest.mark.asyncio
async def test_cache_prefix_prepends_and_enables_cache() -> None:
    provider = _CapturingProvider()
    prefix = AuxCachePrefix(
        messages=(
            ChatMessage(role=ChatRole.SYSTEM, content="общий системный префикс"),
        ),
        enable_prompt_cache=True,
    )
    await aux_completion(
        provider=provider, messages=_msgs("хвост"), model="m", cache_prefix=prefix
    )
    req = provider.requests[0]
    # Parent prefix is prepended verbatim + cache turned on (rides parent cache).
    assert req.messages[0].content == "общий системный префикс"
    assert req.messages[-1].content == "хвост"
    assert req.enable_prompt_cache is True


@pytest.mark.asyncio
async def test_no_cache_prefix_leaves_cache_off() -> None:
    provider = _CapturingProvider()
    await aux_completion(provider=provider, messages=_msgs(), model="m")
    assert provider.requests[0].enable_prompt_cache is False
    assert len(provider.requests[0].messages) == 1


def test_merge_aux_usage_tags_task_and_is_noop_on_missing() -> None:
    ledger = CostLedger()
    resp = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content="x"),
        usage=UsageSummary(input_tokens=10, output_tokens=5, model_name="mx"),
        model="mx",
        provider="p",
    )
    tagged = merge_aux_usage(ledger, resp, task="memory_extraction")
    assert tagged is not None and tagged.metadata["aux_task"] == "memory_extraction"
    assert ledger.per_model["mx"].input_tokens == 10
    # No-ops: None ledger / None response / usage without model name.
    assert merge_aux_usage(None, resp, task="t") is None
    assert merge_aux_usage(ledger, None, task="t") is None
    no_model = LlmResponse(
        message=ChatMessage(role=ChatRole.ASSISTANT, content="x"),
        usage=UsageSummary(input_tokens=1, output_tokens=1, model_name=""),
        model="",
        provider="p",
    )
    assert merge_aux_usage(ledger, no_model, task="t") is None


def test_fork_event_payload_is_raw_free_with_hit_rate() -> None:
    resp = LlmResponse(
        message=ChatMessage(
            role=ChatRole.ASSISTANT, content="секрет не должен попасть"
        ),
        usage=UsageSummary(
            input_tokens=100, output_tokens=20, cache_read_tokens=300, model_name="m"
        ),
        model="m",
        provider="p",
    )
    payload = aux_fork_event_payload(resp, task="titles")
    assert payload["raw_free"] is True
    assert payload["aux_task"] == "titles"
    assert payload["input_tokens"] == 100 and payload["cache_read_tokens"] == 300
    # hit rate = cache_read / (input + cache_read) = 300/400.
    assert payload["cache_hit_rate"] == 0.75
    # No text leaked.
    assert "секрет" not in str(payload)


@pytest.mark.asyncio
async def test_structured_completion_merges_usage_when_ledger_given() -> None:
    from agent_driver.llm.structured import structured_completion

    schema = {
        "type": "object",
        "properties": {"x": {"type": "string"}},
        "required": ["x"],
    }

    class _EmitProvider(FakeProvider):
        async def complete(self, request: LlmRequest) -> LlmResponse:
            response = await super().complete(request)
            meta = dict(response.metadata or {})
            meta["planned_tool_calls"] = [
                {"tool_name": "emit_result", "args": {"x": "v"}}
            ]
            return response.model_copy(update={"metadata": meta})

    ledger = CostLedger()
    result = await structured_completion(
        provider=_EmitProvider(response_text=""),
        messages=_msgs(),
        schema=schema,
        model="m2",
        cost_ledger=ledger,
        task="memory_extraction",
    )
    assert result == {"x": "v"}
    assert ledger.per_model["m2"].api_calls == 1
