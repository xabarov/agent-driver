"""R1 — Anthropic native thinking translation from the neutral reasoning envelope.

Pins the exact wire shapes so a live-API drift is caught here, not in production:
adaptive-era Claude (4.6+) emits ``thinking:{type:adaptive,display:summarized}`` +
``output_config:{effort:tier}``; legacy Claude (<=4.5) emits
``thinking:{type:enabled,budget_tokens:N}`` with the mandated ``temperature=1`` and a
``max_tokens`` floor above the budget. Everything is opt-in (a None/absent envelope is a
strict no-op).
"""

from __future__ import annotations

import pytest

from agent_driver.llm.contracts import LlmRequest
from agent_driver.llm.providers_impl.anthropic import (
    AnthropicProvider,
    _apply_anthropic_thinking,
)


def _payload(model: str, **over):
    p = {"model": model, "max_tokens": 4096}
    p.update(over)
    return p


def test_adaptive_model_emits_output_config():
    p = _payload("claude-opus-4-8")
    _apply_anthropic_thinking(p, {"effort": "high"})
    assert p["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert p["output_config"] == {"effort": "high"}
    assert "budget_tokens" not in p["thinking"]


def test_xhigh_downgrades_to_max_on_46_family():
    p = _payload("claude-sonnet-4-6")
    _apply_anthropic_thinking(p, {"effort": "xhigh"})
    assert p["output_config"] == {"effort": "max"}


def test_xhigh_kept_on_newer_model():
    p = _payload("claude-opus-4-8")
    _apply_anthropic_thinking(p, {"effort": "xhigh"})
    assert p["output_config"] == {"effort": "xhigh"}


def test_legacy_model_emits_budget_tokens_temp1_and_floors_max_tokens():
    p = _payload("claude-sonnet-4-5", temperature=0.2)
    _apply_anthropic_thinking(p, {"effort": "high"})
    assert p["thinking"] == {"type": "enabled", "budget_tokens": 16000}
    assert p["temperature"] == 1
    assert p["max_tokens"] == 16000 + 4096
    assert "output_config" not in p


def test_legacy_does_not_reduce_already_large_max_tokens():
    p = _payload("claude-3-7-sonnet", max_tokens=64000)
    _apply_anthropic_thinking(p, {"effort": "low"})
    assert p["max_tokens"] == 64000


def test_raw_budget_envelope_maps_to_adaptive_tier():
    p = _payload("claude-opus-4-8")
    _apply_anthropic_thinking(p, {"max_tokens": 20000})
    assert p["output_config"] == {"effort": "high"}


def test_haiku_is_noop():
    p = _payload("claude-3-5-haiku-latest")
    _apply_anthropic_thinking(p, {"effort": "max"})
    assert "thinking" not in p
    assert "output_config" not in p


@pytest.mark.parametrize("reasoning", [None, {}, {"enabled": False}])
def test_disabled_or_absent_is_noop(reasoning):
    p = _payload("claude-opus-4-8")
    _apply_anthropic_thinking(p, reasoning)
    assert "thinking" not in p
    assert "output_config" not in p


def test_provider_request_payload_includes_thinking():
    provider = AnthropicProvider(
        config=AnthropicProvider.Config(api_key="sk-test", model="claude-opus-4-8")
    )
    req = LlmRequest(messages=[], reasoning={"effort": "high"})
    payload = provider._request_payload(req, stream=False)
    assert payload["thinking"] == {"type": "adaptive", "display": "summarized"}
    assert payload["output_config"] == {"effort": "high"}


def test_provider_payload_unchanged_without_reasoning():
    provider = AnthropicProvider(
        config=AnthropicProvider.Config(api_key="sk-test", model="claude-opus-4-8")
    )
    req = LlmRequest(messages=[])
    payload = provider._request_payload(req, stream=False)
    assert "thinking" not in payload
    assert "output_config" not in payload
