"""Raw-free redaction of content-bearing span attributes (PhoenixTracingConfig.redact_io)."""

from agent_driver.observability import openinference as oi
from agent_driver.observability import phoenix


class _FakeSpan:
    def __init__(self):
        self.attrs = {}

    def set_attribute(self, key, value):
        self.attrs[key] = value


def test_content_keys_redacted_when_enabled(monkeypatch):
    monkeypatch.setattr(phoenix, "_TRACING_REDACT_IO", True)
    span = _FakeSpan()
    oi._set(span, "input.value", "raw prompt text")
    oi._set(span, "output.value", "raw answer text")
    oi._set(span, "llm.input_messages.0.message.content", "raw user message")
    oi._set(span, "llm.output_messages.0.message.content", "raw assistant message")
    oi._set(span, "tool_call.function.arguments", '{"q": "secret"}')
    # metadata must NOT be redacted
    oi._set(span, "llm.model_name", "gpt-x")
    oi._set(span, "llm.token_count.total", 42)
    oi._set(span, "llm.input_messages.0.message.role", "user")

    assert span.attrs["input.value"] == oi._REDACT_PLACEHOLDER
    assert span.attrs["output.value"] == oi._REDACT_PLACEHOLDER
    assert span.attrs["llm.input_messages.0.message.content"] == oi._REDACT_PLACEHOLDER
    assert span.attrs["llm.output_messages.0.message.content"] == oi._REDACT_PLACEHOLDER
    assert span.attrs["tool_call.function.arguments"] == oi._REDACT_PLACEHOLDER
    assert span.attrs["llm.model_name"] == "gpt-x"
    assert span.attrs["llm.token_count.total"] == 42
    assert span.attrs["llm.input_messages.0.message.role"] == "user"


def test_content_kept_when_disabled(monkeypatch):
    monkeypatch.setattr(phoenix, "_TRACING_REDACT_IO", False)
    span = _FakeSpan()
    oi._set(span, "input.value", "raw prompt text")
    oi._set(span, "llm.output_messages.0.message.content", "raw answer")
    assert span.attrs["input.value"] == "raw prompt text"
    assert span.attrs["llm.output_messages.0.message.content"] == "raw answer"


def test_config_carries_redact_flag():
    cfg = phoenix.PhoenixTracingConfig(enabled=True, redact_io=True)
    assert cfg.redact_io is True
    assert phoenix.PhoenixTracingConfig().redact_io is False
