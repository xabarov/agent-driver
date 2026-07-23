"""Epic 028: cache usage dialects, marker placement, break forensics."""

from __future__ import annotations

from types import SimpleNamespace

from agent_driver.contracts.messages import ChatMessage
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible.normalization import (
    extract_cache_token_fields,
    extract_usage,
)
from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
)


def test_cache_fields_openai_dialect() -> None:
    usage = {"prompt_tokens": 100, "prompt_tokens_details": {"cached_tokens": 60}}
    assert extract_cache_token_fields(usage) == (60, None)


def test_cache_fields_anthropic_dialect() -> None:
    usage = {"cache_read_input_tokens": 500, "cache_creation_input_tokens": 1200}
    assert extract_cache_token_fields(usage) == (500, 1200)


def test_cache_fields_deepseek_dialect_and_absent() -> None:
    assert extract_cache_token_fields({"prompt_cache_hit_tokens": 42}) == (42, None)
    # Provider reports nothing → None, not fabricated zeros (honesty rule).
    assert extract_cache_token_fields({"prompt_tokens": 10}) == (None, None)


def test_extract_usage_populates_cache_fields() -> None:
    summary = extract_usage(
        {"usage": {"prompt_tokens": 100, "completion_tokens": 5,
                   "prompt_tokens_details": {"cached_tokens": 80}}},
        provider="openrouter",
        model="m",
    )
    assert summary.cache_read_tokens == 80
    assert summary.cache_creation_tokens is None


def _payload(messages: list[ChatMessage], *, cache: bool) -> dict:
    request = LlmRequest(messages=messages, enable_prompt_cache=cache)
    return build_openai_completion_payload(
        request, model="m", max_tokens_default=None, extra_body={}, stream=False
    )


def test_markers_system_and_last3_skip_tool_and_empty() -> None:
    messages = [
        ChatMessage(role="system", content="системный промпт"),
        ChatMessage(role="user", content="вопрос раз"),
        ChatMessage(role="assistant", content="ответ раз"),
        ChatMessage(role="assistant", content="", metadata={"tool_calls": [
            {"id": "t1", "type": "function",
             "function": {"name": "f", "arguments": "{}"}}]}),
        ChatMessage(role="tool", content="большой tool-результат", tool_call_id="t1"),
        ChatMessage(role="user", content="вопрос два"),
    ]
    rows = _payload(messages, cache=True)["messages"]
    def marked(row):
        c = row.get("content")
        return isinstance(c, list) and any(
            isinstance(p, dict) and "cache_control" in p for p in c
        )
    # system carries a marker
    assert marked(rows[0])
    # role:tool NEVER carries one (OpenRouter hang quirk)
    assert not marked(rows[4])
    # empty pure-tool_calls assistant row is not a carrier (wasted breakpoint)
    assert not marked(rows[3])
    # last 3 eligible content rows: вопрос два, ответ раз, вопрос раз
    assert marked(rows[5]) and marked(rows[2]) and marked(rows[1])
    # tool_calls metadata survived the content conversion
    assert rows[3]["tool_calls"][0]["function"]["name"] == "f"


def test_markers_absent_when_cache_disabled() -> None:
    rows = _payload(
        [ChatMessage(role="system", content="s"), ChatMessage(role="user", content="u")],
        cache=False,
    )["messages"]
    assert all(isinstance(r.get("content"), str) for r in rows)


def test_cache_break_forensics_warns_only_on_stable_prefix_drop() -> None:
    from agent_driver.runtime.single_agent.llm_step.cache_forensics import (
        check_prompt_cache_break,
    )

    emitted = []
    host = SimpleNamespace(_emit=emitted.append)
    context = SimpleNamespace(metadata={}, run_id="r", attempt_id="a")
    request = LlmRequest(messages=[ChatMessage(role="system", content="stable")])

    def usage(read):
        return SimpleNamespace(cache_read_tokens=read)

    check_prompt_cache_break(host, context, request, usage(50000))  # baseline
    check_prompt_cache_break(host, context, request, usage(49000))  # small drop
    assert emitted == []
    check_prompt_cache_break(host, context, request, usage(10000))  # big drop, same prefix
    assert len(emitted) == 1
    payload = emitted[0].payload
    assert payload["signal_id"] == "prompt_cache_broken"
    assert payload["dropped_tokens"] == 39000

    # Prefix changed on purpose → expected break, no warning.
    emitted.clear()
    request2 = LlmRequest(messages=[ChatMessage(role="system", content="CHANGED")])
    check_prompt_cache_break(host, context, request2, usage(0))
    assert emitted == []

    # Provider without cache fields → forensics stays silent entirely.
    emitted.clear()
    context2 = SimpleNamespace(metadata={}, run_id="r", attempt_id="a")
    check_prompt_cache_break(host, context2, request, SimpleNamespace(cache_read_tokens=None))
    assert emitted == [] and "prompt_cache_state" not in context2.metadata
