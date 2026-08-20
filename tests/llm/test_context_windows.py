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

    def test_unknown_model_falls_back_to_modern_window(self):
        # BUG-2 fix: an unresolved model id no longer silently keeps the legacy 12k
        # (which fires compaction/pressure absurdly early); it falls back to a modern
        # window and flags the source so the runtime can warn.
        s = TrimmingSettings().resolved_for_model("nope")
        assert s.context_window_estimate == 128_000
        assert s.context_window_source == "unresolved_fallback"

    def test_explicit_window_is_authoritative_over_fallback(self):
        s = TrimmingSettings.for_context_window(8_000).resolved_for_model("nope")
        assert s.context_window_estimate == 8_000
        assert s.context_window_source == "explicit"


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


def test_default_window_estimate_is_single_sourced():
    # BUG-2 (c): the pre-resolution 12k default was a bare literal duplicated across
    # config / token-pressure / build. It now lives in ONE place; assert the three
    # field defaults all read from it so a future edit can't reintroduce drift.
    from agent_driver.context.token_estimation import DEFAULT_CONTEXT_WINDOW_ESTIMATE
    from agent_driver.context.token_pressure import TokenPressureInput
    from agent_driver.runtime.single_agent.lifecycle.config_sections import (
        DEFAULT_CONTEXT_WINDOW_ESTIMATE as CONFIG_DEFAULT,
        TrimmingSettings,
    )
    from dataclasses import fields

    from agent_driver.runtime.single_agent.llm_step.build import LlmRequestBuildContext

    assert CONFIG_DEFAULT is DEFAULT_CONTEXT_WINDOW_ESTIMATE
    assert TrimmingSettings().context_window_estimate == DEFAULT_CONTEXT_WINDOW_ESTIMATE
    assert (
        TokenPressureInput(prompt_messages=()).context_window_estimate
        == DEFAULT_CONTEXT_WINDOW_ESTIMATE
    )
    # LlmRequestBuildContext requires run_input, so read its field default directly.
    build_default = next(
        f.default
        for f in fields(LlmRequestBuildContext)
        if f.name == "context_window_estimate"
    )
    assert build_default == DEFAULT_CONTEXT_WINDOW_ESTIMATE
