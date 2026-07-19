"""Per-model context-window resolution (epic 017)."""

from __future__ import annotations

from agent_driver.llm.context_windows import (
    MIN_RESOLVED_CONTEXT_WINDOW,
    provider_model_hint,
    resolve_context_window,
)
from agent_driver.runtime.single_agent.lifecycle.config_sections import TrimmingSettings


def test_catalog_exact_and_alias_and_vendor_prefix():
    assert resolve_context_window("gpt-4.1") == 128_000
    assert resolve_context_window("openai/gpt-5.5") == 400_000
    # Vendor-prefixed id falls back to the bare catalog row.
    assert resolve_context_window("deepseek/deepseek-reasoner") == 64_000


def test_family_fallbacks_and_floor():
    assert resolve_context_window("deepseek/deepseek-v4-flash") == 128_000
    assert resolve_context_window("claude-opus-4-8") == 200_000
    assert resolve_context_window("unknown-model") is None
    assert resolve_context_window("") is None
    assert resolve_context_window(None) is None
    assert MIN_RESOLVED_CONTEXT_WINDOW <= 64_000


def test_provider_model_hint_probes_public_then_private():
    class _Public:
        model = "deepseek/deepseek-v4-flash"

    class _Private:
        _model = "claude-sonnet"

    class _Neither:
        name = "x"

    assert provider_model_hint(_Public()) == "deepseek/deepseek-v4-flash"
    assert provider_model_hint(_Private()) == "claude-sonnet"
    assert provider_model_hint(_Neither()) is None


class TestResolvedForModel:
    def test_defaults_autoscale_to_model_window(self):
        s = TrimmingSettings().resolved_for_model("deepseek/deepseek-v4-flash")
        assert s.context_window_estimate == 128_000
        assert s.token_compact_threshold == 96_000
        assert s.context_window_source == "model_catalog"

    def test_trim_fields_preserved(self):
        s = TrimmingSettings(trim_max_chars=24_000).resolved_for_model("claude-x")
        assert s.context_window_estimate == 200_000
        assert s.trim_max_chars == 24_000

    def test_explicit_host_window_wins(self):
        s = TrimmingSettings.for_context_window(50_000).resolved_for_model("claude-x")
        assert s.context_window_estimate == 50_000
        assert s.context_window_source == "explicit"

    def test_bespoke_direct_window_wins(self):
        s = TrimmingSettings(context_window_estimate=30_000).resolved_for_model(
            "claude-x"
        )
        assert s.context_window_estimate == 30_000

    def test_unknown_model_keeps_defaults(self):
        s = TrimmingSettings().resolved_for_model("nope")
        assert s.context_window_estimate == 12_000
        assert s.context_window_source == "default"


def test_provider_model_hint_prefers_explicit_protocol():
    class _WithHint:
        _model = "attribute-model"

        def model_hint(self):
            return "protocol-model"

    class _WrapperAroundHint:
        def __init__(self):
            self.provider = _WithHint()

    assert provider_model_hint(_WithHint()) == "protocol-model"
    assert provider_model_hint(_WrapperAroundHint()) == "protocol-model"
