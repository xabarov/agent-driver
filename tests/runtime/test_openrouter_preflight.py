"""OpenRouter preflight ladder tests."""

from __future__ import annotations

import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from agent_driver.contracts.messages import ChatMessage, ChatRole
from agent_driver.contracts.usage import UsageSummary
from agent_driver.llm.contracts import LlmFinishReason, LlmResponse
from agent_driver.runtime import openrouter_preflight
from agent_driver.runtime.openrouter_preflight import run_openrouter_preflight_ladder


@pytest.mark.asyncio
async def test_openrouter_preflight_ladder_writes_skipped_live_artifacts(tmp_path) -> None:
    result = await run_openrouter_preflight_ladder(
        output_dir=tmp_path / "preflight",
        env={},
        live=False,
    )

    assert result["provider"] == "openrouter"
    assert result["provider_preflight"]["preflight"]["status"] == "degraded"
    assert result["request_shape_plan"]["selected_action"] == "reshape_request"
    assert result["live_result"] == {
        "status": "skipped",
        "reason": "live_preflight_not_requested",
    }
    assert result["validation_gates"]["statuses"]["openrouter_live_preflight"] == (
        "skipped"
    )

    root = tmp_path / "preflight"
    assert (root / "manifest.json").is_file()
    benchmark = json.loads((root / "benchmark_report.json").read_text())
    payload = benchmark["openrouter_preflight_ladder"]
    assert payload["redaction"]["contains_api_key"] is False
    assert payload["request_shape_plan"]["reshaped_request"]["tool_choice"] == "auto"


@pytest.mark.asyncio
async def test_openrouter_preflight_ladder_skips_live_without_key(tmp_path) -> None:
    result = await run_openrouter_preflight_ladder(
        output_dir=tmp_path / "preflight",
        env={},
        live=True,
    )

    assert result["live_result"] == {
        "status": "skipped",
        "reason": "openrouter_api_key_missing",
    }
    gate = result["validation_gates"]["gates"][2]
    assert gate["gate_id"] == "openrouter_live_preflight"
    assert gate["status"] == "skipped"
    assert gate["redacted_metadata"]["api_key_configured"] is False


@pytest.mark.asyncio
async def test_openrouter_preflight_ladder_accepts_llm_api_key(
    tmp_path,
    monkeypatch,
) -> None:
    class ProviderStub:
        class Config:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def __init__(self, config):
            self.config = config

        async def healthcheck(self):
            return SimpleNamespace(healthy=False, latency_ms=7.0)

    monkeypatch.setattr(
        openrouter_preflight,
        "OpenAICompatibleProvider",
        ProviderStub,
    )

    result = await run_openrouter_preflight_ladder(
        output_dir=tmp_path / "preflight",
        env={"LLM_API_KEY": "sk-or-v1-test"},
        live=True,
    )

    assert result["live_result"]["reason"] == "openrouter_healthcheck_failed"
    gate = result["validation_gates"]["gates"][2]
    assert gate["redacted_metadata"]["api_key_configured"] is True


@pytest.mark.asyncio
async def test_openrouter_preflight_ladder_live_success_without_response_latency_attr(
    tmp_path,
    monkeypatch,
) -> None:
    class ProviderStub:
        class Config:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def __init__(self, config):
            self.config = config

        async def healthcheck(self):
            return SimpleNamespace(healthy=True, latency_ms=11.0)

        async def complete(self, request):
            return LlmResponse(
                message=ChatMessage(role=ChatRole.ASSISTANT, content='{"ok": true}'),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(input_tokens=1, output_tokens=2),
                provider="openrouter",
                model="test-model",
                metadata={"provider_request_id": "req_1"},
            )

    monkeypatch.setattr(
        openrouter_preflight,
        "OpenAICompatibleProvider",
        ProviderStub,
    )

    result = await run_openrouter_preflight_ladder(
        output_dir=tmp_path / "preflight",
        env={"LLM_API_KEY": "sk-or-v1-test", "AGENT_DRIVER_MODEL": "test-model"},
        live=True,
    )

    assert result["live_result"]["status"] == "passed"
    assert result["live_result"]["latency_ms"] == 11.0
    assert result["live_result"]["provider_request_id_present"] is True


@pytest.mark.asyncio
async def test_openrouter_preflight_ladder_writes_phoenix_gate_for_live_trace(
    tmp_path,
    monkeypatch,
) -> None:
    class ProviderStub:
        class Config:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

        def __init__(self, config):
            self.config = config

        async def healthcheck(self):
            return SimpleNamespace(healthy=True, latency_ms=5.0)

        async def complete(self, request):
            return LlmResponse(
                message=ChatMessage(role=ChatRole.ASSISTANT, content='{"ok": true}'),
                finish_reason=LlmFinishReason.STOP,
                usage=UsageSummary(input_tokens=3, output_tokens=4),
                provider="openrouter",
                model="test-model",
                metadata={},
            )

    class SpanStub:
        @staticmethod
        def get_span_context():
            return SimpleNamespace(trace_id=0x123)

        @staticmethod
        def set_attribute(*args, **kwargs):
            return None

        @staticmethod
        def set_status(*args, **kwargs):
            return None

    @contextmanager
    def oi_span_stub(*args, **kwargs):
        yield SpanStub()

    monkeypatch.setattr(openrouter_preflight, "OpenAICompatibleProvider", ProviderStub)
    monkeypatch.setattr(openrouter_preflight, "oi_span", oi_span_stub)
    monkeypatch.setattr(
        openrouter_preflight,
        "setup_phoenix_tracing",
        lambda _config: {"enabled": True, "error": None},
    )

    result = await run_openrouter_preflight_ladder(
        output_dir=tmp_path / "preflight",
        env={"LLM_API_KEY": "sk-or-v1-test"},
        live=True,
        phoenix_endpoint="http://127.0.0.1:6006",
        phoenix_gate_output_dir=tmp_path / "phoenix-gate",
    )

    assert result["live_result"]["phoenix_trace_id"] == f"{0x123:032x}"
    assert result["phoenix_gate"]["gate"]["status"] == "passed"
    gate_payload = json.loads(
        (tmp_path / "phoenix-gate" / "validation_gates.json").read_text()
    )
    assert gate_payload["statuses"]["phoenix_trace"] == "passed"
