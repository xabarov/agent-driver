"""Offline normalization tests for provider payload adapters."""

from __future__ import annotations

from agent_driver.llm.contracts import LlmFinishReason
from agent_driver.llm.ollama import (
    OllamaProvider,
    normalize_ollama_completion_payload,
    normalize_ollama_stream_chunk,
)
from agent_driver.llm.openai_compatible import (
    OpenAICompatibleProvider,
    normalize_openai_completion_payload,
    normalize_openai_stream_chunk,
)


def test_openai_completion_normalization_from_fixture() -> None:
    """Normalize OpenAI-compatible completion payload into neutral response."""
    payload = {
        "model": "gpt-test",
        "choices": [
            {
                "message": {"role": "assistant", "content": "hello"},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
    }
    response = normalize_openai_completion_payload(
        payload, provider_name="openai-compat", fallback_model="fallback"
    )
    response_payload = response.model_dump()
    assert response.message.content == "hello"
    assert response.finish_reason == LlmFinishReason.STOP
    assert response_payload["usage"]["total_tokens"] == 5


def test_openai_stream_chunk_normalization_from_fixture() -> None:
    """Normalize OpenAI-compatible stream chunk into neutral stream event."""
    chunk = {
        "model": "gpt-test",
        "choices": [{"delta": {"content": "hel"}, "finish_reason": None}],
    }
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openai-compat", fallback_model="fallback"
    )
    assert event.event == "delta"
    assert event.delta_text == "hel"
    assert event.finish_reason is None


def test_ollama_completion_normalization_from_fixture() -> None:
    """Normalize Ollama completion payload into neutral response."""
    payload = {
        "model": "llama3:8b",
        "message": {"content": "done"},
        "prompt_eval_count": 4,
        "eval_count": 6,
    }
    response = normalize_ollama_completion_payload(
        payload, provider_name="ollama", fallback_model="fallback"
    )
    response_payload = response.model_dump()
    assert response.message.content == "done"
    assert response_payload["usage"]["total_tokens"] == 10


def test_ollama_stream_chunk_normalization_from_fixture() -> None:
    """Normalize Ollama stream done chunk and usage metadata."""
    chunk = {
        "message": {"content": ""},
        "done": True,
        "prompt_eval_count": 2,
        "eval_count": 3,
    }
    event = normalize_ollama_stream_chunk(
        chunk, provider_name="ollama", fallback_model="llama3:8b"
    )
    event_payload = event.model_dump()
    assert event.event == "done"
    assert event.finish_reason == LlmFinishReason.STOP
    assert event.usage is not None
    assert event_payload["usage"]["total_tokens"] == 5


def test_provider_config_constructors() -> None:
    """Provider config wrappers should initialize adapters correctly."""
    openai_provider = OpenAICompatibleProvider(
        config=OpenAICompatibleProvider.Config(
            name="openai",
            base_url="https://example.local/v1",
            api_key="token",
            model="gpt-test",
        )
    )
    ollama_provider = OllamaProvider()

    assert openai_provider.name == "openai"
    assert ollama_provider.name == "ollama"
