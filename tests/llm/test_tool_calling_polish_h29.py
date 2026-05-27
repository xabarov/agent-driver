"""Phase 13 H29 — tests for explicit tool-calling controls on OpenAI-compat.

Pins:
  * Default (parallel_tool_calls=None): payload does NOT include the key —
    backend's default applies. Backwards compat with pre-H29 callers.
  * Explicit True: payload includes ``parallel_tool_calls: True``.
  * Explicit False: payload includes ``parallel_tool_calls: False`` — forces
    sequential execution for cases where the model misbehaves with parallel.
  * Field only emitted when tools are present (no tools → no parallel knob).
"""

from __future__ import annotations

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible import OpenAICompatibleProvider


def _make_provider() -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="test",
            api_key="test",
            model="gpt-4o-mini",
            base_url="https://example.com/v1",
        )
    )


def _make_request(
    *,
    parallel_tool_calls: bool | None = None,
    tools: list[dict] | None = None,
) -> LlmRequest:
    return LlmRequest(
        messages=[ChatMessage(role=ChatRole.USER, content="hello")],
        tools=tools or [],
        parallel_tool_calls=parallel_tool_calls,
    )


def test_default_omits_parallel_tool_calls_key():
    """Pre-H29 compat: None (default) → key absent → backend's default."""
    provider = _make_provider()
    payload = provider._payload(
        _make_request(
            tools=[{"type": "function", "function": {"name": "x"}}],
        ),
        stream=False,
    )
    assert "parallel_tool_calls" not in payload


def test_explicit_true_emits_parallel_tool_calls_true():
    provider = _make_provider()
    payload = provider._payload(
        _make_request(
            parallel_tool_calls=True,
            tools=[{"type": "function", "function": {"name": "x"}}],
        ),
        stream=False,
    )
    assert payload["parallel_tool_calls"] is True


def test_explicit_false_emits_parallel_tool_calls_false():
    provider = _make_provider()
    payload = provider._payload(
        _make_request(
            parallel_tool_calls=False,
            tools=[{"type": "function", "function": {"name": "x"}}],
        ),
        stream=False,
    )
    assert payload["parallel_tool_calls"] is False


def test_no_tools_no_parallel_tool_calls_key():
    """parallel_tool_calls is a knob ON the tool-calling pathway. Without
    tools, emitting it would be meaningless / could trip strict backends."""
    provider = _make_provider()
    payload = provider._payload(
        _make_request(parallel_tool_calls=True, tools=[]),
        stream=False,
    )
    assert "parallel_tool_calls" not in payload


def test_default_field_value_is_none():
    request = LlmRequest(messages=[ChatMessage(role=ChatRole.USER, content="x")])
    assert request.parallel_tool_calls is None
