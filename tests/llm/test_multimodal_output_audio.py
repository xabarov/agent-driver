"""Output audio end-to-end: provider assistant ``audio`` -> server message.audio.

Covers the response side (the model *returns* audio) and the request side
(``modalities`` / ``audio`` forwarded to the provider).
"""

from __future__ import annotations

from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.openai_compatible.normalization import (
    normalize_openai_completion_payload,
    normalize_openai_stream_chunk,
)
from agent_driver.runtime.single_agent.llm_step.streaming import (
    _accumulate_audio_delta,
    _finalize_stream_audio,
)
from agent_driver.llm.providers_impl.openai_compatible.payload import (
    build_openai_completion_payload,
)
from agent_driver.server.openai import translate
from agent_driver.server.openai.schema import ChatCompletionRequest

_AUDIO = {
    "id": "audio_abc",
    "data": "UklGRiQ=",
    "transcript": "Hello there.",
    "expires_at": 1234567890,
    "format": "wav",
}


# --- response side ----------------------------------------------------------


def test_normalize_extracts_assistant_audio() -> None:
    payload = {
        "choices": [{"message": {"role": "assistant", "content": "hi", "audio": _AUDIO}}],
        "model": "gpt-audio",
    }
    resp = normalize_openai_completion_payload(
        payload, provider_name="openrouter", fallback_model="gpt-audio"
    )
    assert resp.message.metadata["output_audio"] == _AUDIO
    assert resp.message.content == "hi"


def test_normalize_falls_back_to_transcript_when_content_null() -> None:
    payload = {
        "choices": [{"message": {"role": "assistant", "content": None, "audio": _AUDIO}}],
        "model": "gpt-audio",
    }
    resp = normalize_openai_completion_payload(
        payload, provider_name="openrouter", fallback_model="gpt-audio"
    )
    # content was null -> the transcript becomes the message text.
    assert resp.message.content == "Hello there."
    assert resp.message.metadata["output_audio"]["id"] == "audio_abc"


def test_normalize_no_audio_leaves_metadata_clean() -> None:
    payload = {"choices": [{"message": {"role": "assistant", "content": "hi"}}]}
    resp = normalize_openai_completion_payload(
        payload, provider_name="p", fallback_model="m"
    )
    assert "output_audio" not in resp.message.metadata


# completion_object emitting ``message.audio`` is covered end-to-end in
# tests/server/test_openai_server.py (it needs a valid terminal AgentRunOutput,
# which the runtime builds for us through the FakeProvider path).


# --- streaming response side ------------------------------------------------


def test_stream_chunk_carries_audio_delta() -> None:
    chunk = {"choices": [{"delta": {"audio": {"data": "AAA=", "transcript": "He"}}}]}
    event = normalize_openai_stream_chunk(
        chunk, provider_name="openrouter", fallback_model="gpt-audio"
    )
    assert event.metadata["output_audio_delta"] == {"data": "AAA=", "transcript": "He"}


def test_finalize_stream_audio_accumulates_segments() -> None:
    state: dict = {}
    # "He" + "llo" transcript; pcm16 base64 data split across two segments.
    _accumulate_audio_delta(
        state, {"id": "a1", "data": "aGVs", "transcript": "He", "format": "pcm16"}
    )
    _accumulate_audio_delta(state, {"data": "bG8=", "transcript": "llo"})
    audio = _finalize_stream_audio(state)
    assert audio["id"] == "a1"
    assert audio["transcript"] == "Hello"
    assert audio["format"] == "pcm16"
    # "aGVs" + "bG8=" -> bytes b"hello" -> re-encoded canonical base64.
    import base64 as _b64

    assert _b64.b64decode(audio["data"]) == b"hello"


def test_finalize_stream_audio_empty_returns_none() -> None:
    assert _finalize_stream_audio({}) is None


# --- request side -----------------------------------------------------------


def test_provider_extra_body_collects_modalities_and_audio() -> None:
    req = ChatCompletionRequest(
        model="gpt-audio",
        messages=[],
        modalities=["text", "audio"],
        audio={"voice": "alloy", "format": "wav"},
    )
    assert req.provider_extra_body() == {
        "modalities": ["text", "audio"],
        "audio": {"voice": "alloy", "format": "wav"},
    }


def test_provider_extra_body_empty_when_absent() -> None:
    req = ChatCompletionRequest(model="m", messages=[])
    assert req.provider_extra_body() == {}


def test_to_run_input_routes_extra_body_into_app_metadata() -> None:
    req = ChatCompletionRequest(
        model="gpt-audio",
        messages=[{"role": "user", "content": "say hi"}],
        modalities=["text", "audio"],
        audio={"voice": "alloy", "format": "mp3"},
    )
    run_input = translate.to_run_input(
        req, run_id="r1", agent_id="ag", graph_preset="single_agent"
    )
    assert run_input.app_metadata["provider_extra_body"] == {
        "modalities": ["text", "audio"],
        "audio": {"voice": "alloy", "format": "mp3"},
    }


def test_payload_merges_request_extra_body_into_modalities_audio() -> None:
    # The build step lands modalities/audio in request.metadata.provider_extra_body;
    # the payload builder must surface them as top-level OpenAI params.
    request = LlmRequest(
        messages=[],
        model="gpt-audio",
        metadata={
            "provider_extra_body": {
                "modalities": ["text", "audio"],
                "audio": {"voice": "alloy", "format": "wav"},
            }
        },
    )
    payload = build_openai_completion_payload(
        request, model="gpt-audio", max_tokens_default=None, extra_body={}, stream=False
    )
    assert payload["modalities"] == ["text", "audio"]
    assert payload["audio"] == {"voice": "alloy", "format": "wav"}
