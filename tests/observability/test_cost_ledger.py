"""Phase 12 H20 — tests for the per-(model, session) cost ledger.

Pins:
* ``estimate_cost_usd`` honors explicit ``cost_usd_estimate`` from
  the provider;
* lookup falls back to the pricing registry when not set;
* unknown model returns 0.0 (don't extrapolate from nothing);
* per-model tally accumulates input/output/cache tokens + cost;
* CostLedger total_cost_usd / total_tokens compute correctly;
* per-tool duration tracking accumulates;
* register_pricing overrides built-in defaults;
* round-trips through Pydantic for checkpoint persistence.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from agent_driver.contracts.usage import UsageSummary
from agent_driver.observability.cost_ledger import (
    CostLedger,
    ModelTokenTally,
    Pricing,
    estimate_cost_usd,
    lookup_pricing,
    register_pricing,
)


def _usage(
    *,
    model="claude-haiku-4-5-20251001",
    input_tokens=1000,
    output_tokens=500,
    cache_read=None,
    cache_creation=None,
    cost=None,
) -> UsageSummary:
    return UsageSummary(
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read,
        cache_creation_tokens=cache_creation,
        cost_usd_estimate=cost,
        model_name=model,
    )


# -- pricing lookup -------------------------------------------------------


def test_lookup_pricing_returns_known_model():
    p = lookup_pricing("claude-opus-4-7")
    assert p is not None
    assert p.input_per_million == 15.0
    assert p.output_per_million == 75.0


def test_lookup_pricing_unknown_returns_none():
    assert lookup_pricing("unobtainium-foo-1") is None


def test_lookup_pricing_with_whitespace_normalizes():
    p = lookup_pricing("  gpt-4o-mini  ")
    assert p is not None
    assert p.input_per_million == 0.15


# -- estimate_cost_usd ---------------------------------------------------


def test_estimate_uses_explicit_cost_when_set():
    usage = _usage(cost=0.123)
    assert estimate_cost_usd(usage) == 0.123


def test_estimate_uses_pricing_table_when_no_explicit_cost():
    # haiku: $0.8/M in, $4/M out. 1000 in + 500 out → $0.0008 + $0.002 = $0.0028.
    usage = _usage(input_tokens=1000, output_tokens=500, cost=None)
    cost = estimate_cost_usd(usage)
    assert cost == pytest.approx(0.0028, rel=1e-3)


def test_estimate_includes_cache_creation_when_priced():
    # opus: cache_creation $18.75/M. 10K tokens → $0.1875.
    usage = _usage(
        model="claude-opus-4-7",
        input_tokens=0,
        output_tokens=0,
        cache_creation=10000,
        cost=None,
    )
    cost = estimate_cost_usd(usage)
    assert cost == pytest.approx(0.1875, rel=1e-3)


def test_estimate_returns_zero_for_unknown_model_without_explicit_cost():
    usage = _usage(model="mystery-model-z9", cost=None)
    assert estimate_cost_usd(usage) == 0.0


# -- ModelTokenTally ------------------------------------------------------


def test_tally_accumulates_tokens_and_counts_calls():
    tally = ModelTokenTally(model_name="claude-haiku-4-5-20251001")
    tally.accumulate(_usage(input_tokens=100, output_tokens=200))
    tally.accumulate(_usage(input_tokens=300, output_tokens=50, cache_read=20))
    assert tally.input_tokens == 400
    assert tally.output_tokens == 250
    assert tally.cache_read_tokens == 20
    assert tally.api_calls == 2


def test_tally_accumulates_explicit_cost():
    tally = ModelTokenTally(model_name="m")
    tally.accumulate(_usage(model="m", cost=0.5))
    tally.accumulate(_usage(model="m", cost=0.25))
    assert tally.cost_usd == 0.75


# -- CostLedger ---------------------------------------------------------


def test_ledger_routes_usage_to_per_model_tally():
    ledger = CostLedger()
    ledger.accumulate(_usage(model="haiku-a", input_tokens=100, output_tokens=50, cost=0.01))
    ledger.accumulate(_usage(model="haiku-a", input_tokens=200, output_tokens=100, cost=0.02))
    ledger.accumulate(_usage(model="opus-b", input_tokens=50, output_tokens=25, cost=0.5))
    assert set(ledger.per_model.keys()) == {"haiku-a", "opus-b"}
    assert ledger.per_model["haiku-a"].input_tokens == 300
    assert ledger.per_model["haiku-a"].api_calls == 2
    assert ledger.per_model["opus-b"].api_calls == 1


def test_ledger_total_cost_aggregates_across_models():
    ledger = CostLedger()
    ledger.accumulate(_usage(model="a", cost=0.10))
    ledger.accumulate(_usage(model="b", cost=0.05))
    ledger.accumulate(_usage(model="a", cost=0.02))
    assert ledger.total_cost_usd() == pytest.approx(0.17)


def test_ledger_total_tokens_excludes_cache_reads():
    ledger = CostLedger()
    ledger.accumulate(
        _usage(model="m", input_tokens=100, output_tokens=50, cache_read=999)
    )
    # Cache reads NOT counted in total_tokens (they don't bill).
    assert ledger.total_tokens() == 150


def test_ledger_ignores_usage_without_model_name():
    """No-op when usage.model_name is None — protects against partial
    provider responses."""
    ledger = CostLedger()
    ledger.accumulate(
        UsageSummary(input_tokens=100, output_tokens=50, model_name=None)
    )
    assert ledger.per_model == {}


def test_ledger_computes_cost_from_pricing_when_explicit_cost_missing():
    """When provider didn't return cost, ledger falls back to pricing."""
    ledger = CostLedger()
    # haiku: $0.8/M in, $4/M out → 1M in + 500K out = $2.8.
    ledger.accumulate(
        _usage(
            model="claude-haiku-4-5-20251001",
            input_tokens=1_000_000,
            output_tokens=500_000,
            cost=None,
        )
    )
    assert ledger.per_model[
        "claude-haiku-4-5-20251001"
    ].cost_usd == pytest.approx(2.8, rel=1e-3)


# -- per-tool duration --------------------------------------------------


def test_ledger_add_tool_duration_accumulates():
    ledger = CostLedger()
    ledger.add_tool_duration(tool_name="shell", duration_ms=100.0)
    ledger.add_tool_duration(tool_name="shell", duration_ms=50.0)
    ledger.add_tool_duration(tool_name="file_read", duration_ms=25.0)
    assert ledger.per_tool_duration_ms["shell"] == 150.0
    assert ledger.per_tool_duration_ms["file_read"] == 25.0


def test_ledger_ignores_negative_tool_duration():
    """Negative duration is a bug somewhere upstream — ignore silently."""
    ledger = CostLedger()
    ledger.add_tool_duration(tool_name="ok", duration_ms=10.0)
    ledger.add_tool_duration(tool_name="ok", duration_ms=-5.0)
    assert ledger.per_tool_duration_ms["ok"] == 10.0


# -- register_pricing ----------------------------------------------------


def test_register_pricing_overrides_lookup():
    register_pricing("my-custom-model", Pricing(input_per_million=99.0, output_per_million=199.0))
    p = lookup_pricing("my-custom-model")
    assert p is not None
    assert p.input_per_million == 99.0


def test_register_pricing_rejects_empty_name():
    with pytest.raises(ValueError):
        register_pricing("", Pricing(input_per_million=1.0, output_per_million=2.0))
    with pytest.raises(ValueError):
        register_pricing("   ", Pricing(input_per_million=1.0, output_per_million=2.0))


def test_pricing_rejects_negative_values():
    with pytest.raises(ValidationError):
        Pricing(input_per_million=-1.0, output_per_million=10.0)


# -- summary + round-trip ----------------------------------------------


def test_ledger_summary_compact_dict():
    ledger = CostLedger()
    ledger.accumulate(_usage(model="a", input_tokens=100, output_tokens=50, cost=0.05))
    ledger.accumulate(_usage(model="b", input_tokens=200, output_tokens=100, cost=0.10))
    ledger.add_tool_duration(tool_name="shell", duration_ms=42.0)
    summary = ledger.summary()
    assert summary["total_cost_usd"] == 0.15
    assert summary["total_tokens"] == 450
    assert summary["models"] == ["a", "b"]
    assert summary["tool_count"] == 1


def test_ledger_round_trips_through_pydantic():
    ledger = CostLedger()
    ledger.accumulate(_usage(model="a", input_tokens=100, output_tokens=50, cost=0.05))
    ledger.add_tool_duration(tool_name="shell", duration_ms=42.0)
    raw = ledger.model_dump()
    restored = CostLedger.model_validate(raw)
    assert restored.total_cost_usd() == 0.05
    assert restored.per_tool_duration_ms["shell"] == 42.0
    assert restored.per_model["a"].input_tokens == 100
