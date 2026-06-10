"""Phase 12 H20 — per-(model, session) cost ledger.

Aggregates :class:`UsageSummary` instances across the lifetime of a run
into a per-model token tally + USD estimate. Hosts can use this for
operator-visible cost rollups (e.g. "this run cost $0.42") without
having to walk the full event log.

Design:

* :class:`ModelTokenTally` — per-model accumulator. Holds input /
  output / cache_read / cache_creation tokens + total tool duration.
* :class:`CostLedger` — top-level container keyed by canonical model
  name. ``accumulate(usage)`` adds one UsageSummary; ``add_tool_duration``
  records per-tool wallclock; ``total_cost_usd`` returns the rollup.
* :class:`Pricing` + :func:`lookup_pricing` — small built-in table of
  per-1K-token prices. Defaults are conservative ("unknown model" returns
  zero); hosts can register custom pricing via ``register_pricing``.

The ledger is plain Pydantic so it round-trips through
``model_dump`` / ``model_validate`` for checkpoint persistence
(future H20b — wiring into runtime checkpoint store).
"""

from __future__ import annotations

from typing import Any

from pydantic import Field, field_validator

from agent_driver.contracts.base import ContractModel
from agent_driver.contracts.usage import UsageSummary


class ModelTokenTally(ContractModel):
    """Per-model accumulator."""

    model_name: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    api_duration_ms: float = 0.0
    api_calls: int = 0
    cost_usd: float = 0.0

    def accumulate(self, usage: UsageSummary) -> None:
        """Add one UsageSummary into this tally (mutates in place)."""
        self.input_tokens += usage.input_tokens
        self.output_tokens += usage.output_tokens
        self.cache_read_tokens += int(usage.cache_read_tokens or 0)
        self.cache_creation_tokens += int(usage.cache_creation_tokens or 0)
        if usage.cost_usd_estimate is not None:
            self.cost_usd += float(usage.cost_usd_estimate)
        self.api_calls += 1


class Pricing(ContractModel):
    """Per-million-token prices for one model.

    Convention: ``input_per_million`` / ``output_per_million`` are USD
    per 1_000_000 tokens (matches OpenAI / Anthropic public pricing
    pages). Cache pricing is optional — when None, cache_read tokens
    are treated as free (most providers offer cache hits at no charge).
    """

    input_per_million: float = 0.0
    output_per_million: float = 0.0
    cache_read_per_million: float | None = None
    cache_creation_per_million: float | None = None

    @field_validator(
        "input_per_million",
        "output_per_million",
        "cache_read_per_million",
        "cache_creation_per_million",
    )
    @classmethod
    def validate_non_negative(cls, value: float | None) -> float | None:
        if value is None:
            return None
        if value < 0:
            raise ValueError("pricing fields must be non-negative")
        return value


# Built-in pricing table. Conservative defaults; hosts override via
# ``register_pricing(model_name, Pricing(...))`` for their actual rates.
# Numbers correspond to public list prices as of 2026-05; the ledger
# does NOT claim to be billing-accurate — it's an operator-visible
# estimate, not an invoice.
_DEFAULT_PRICING: dict[str, Pricing] = {
    "claude-opus-4-7": Pricing(
        input_per_million=15.0,
        output_per_million=75.0,
        cache_read_per_million=1.5,
        cache_creation_per_million=18.75,
    ),
    "claude-sonnet-4-6": Pricing(
        input_per_million=3.0,
        output_per_million=15.0,
        cache_read_per_million=0.3,
        cache_creation_per_million=3.75,
    ),
    "claude-haiku-4-5-20251001": Pricing(
        input_per_million=0.8,
        output_per_million=4.0,
        cache_read_per_million=0.08,
        cache_creation_per_million=1.0,
    ),
    "gpt-4o": Pricing(input_per_million=2.5, output_per_million=10.0),
    "gpt-4o-mini": Pricing(input_per_million=0.15, output_per_million=0.6),
    # OpenRouter open-weight list prices (per openrouter.ai, 2026-06).
    "qwen/qwen-2.5-7b-instruct": Pricing(
        input_per_million=0.04, output_per_million=0.10
    ),
    "qwen/qwen3.5-397b-a17b": Pricing(input_per_million=0.39, output_per_million=2.34),
}


_pricing_registry: dict[str, Pricing] = dict(_DEFAULT_PRICING)


def register_pricing(model_name: str, pricing: Pricing) -> None:
    """Override or add pricing for a model. Hosts call this once at
    startup to wire their actual contracted rates."""
    if not isinstance(model_name, str) or not model_name.strip():
        raise ValueError("model_name must be a non-empty string")
    _pricing_registry[model_name.strip()] = pricing


def lookup_pricing(model_name: str | None) -> Pricing | None:
    """Return registered pricing for ``model_name`` or ``None``."""
    if not isinstance(model_name, str):
        return None
    return _pricing_registry.get(model_name.strip())


def estimate_cost_usd(usage: UsageSummary) -> float:
    """Estimate USD cost for one UsageSummary using the pricing
    registry. Returns ``usage.cost_usd_estimate`` when set explicitly
    (provider already computed it); otherwise looks up by
    ``model_name``. Unknown models return 0.0.
    """
    if usage.cost_usd_estimate is not None:
        return float(usage.cost_usd_estimate)
    pricing = lookup_pricing(usage.model_name)
    if pricing is None:
        return 0.0
    cost = 0.0
    cost += (usage.input_tokens / 1_000_000.0) * pricing.input_per_million
    cost += (usage.output_tokens / 1_000_000.0) * pricing.output_per_million
    if usage.cache_read_tokens and pricing.cache_read_per_million is not None:
        cost += (usage.cache_read_tokens / 1_000_000.0) * pricing.cache_read_per_million
    if usage.cache_creation_tokens and pricing.cache_creation_per_million is not None:
        cost += (
            usage.cache_creation_tokens / 1_000_000.0
        ) * pricing.cache_creation_per_million
    return cost


class CostLedger(ContractModel):
    """Top-level cost ledger for one run / session."""

    per_model: dict[str, ModelTokenTally] = Field(default_factory=dict)
    per_tool_duration_ms: dict[str, float] = Field(default_factory=dict)
    lines_added: int = 0
    lines_removed: int = 0

    def accumulate(self, usage: UsageSummary) -> None:
        """Add one LLM call's usage into the ledger.

        Looks up or creates the per-model tally; if
        ``usage.cost_usd_estimate`` is set we honor it, otherwise we
        compute via the pricing registry.
        """
        if not usage.model_name:
            return
        tally = self.per_model.get(usage.model_name)
        if tally is None:
            tally = ModelTokenTally(model_name=usage.model_name)
            self.per_model[usage.model_name] = tally
        tally.accumulate(usage)
        if usage.cost_usd_estimate is None:
            computed = estimate_cost_usd(usage)
            tally.cost_usd += computed

    def add_tool_duration(self, *, tool_name: str, duration_ms: float) -> None:
        """Record per-tool wall-clock duration (operator visibility)."""
        if duration_ms < 0:
            return
        existing = self.per_tool_duration_ms.get(tool_name, 0.0)
        self.per_tool_duration_ms[tool_name] = existing + duration_ms

    def total_cost_usd(self) -> float:
        """Sum of cost across all models in the ledger."""
        return sum(tally.cost_usd for tally in self.per_model.values())

    def total_tokens(self) -> int:
        """Sum of input + output tokens (excludes cache reads)."""
        return sum(
            tally.input_tokens + tally.output_tokens
            for tally in self.per_model.values()
        )

    def cache_hit_rate(self) -> float:
        """Fraction of prompt tokens served from cache across all models.

        ``cache_read / (input + cache_read)`` — 0.0 when nothing was cached.
        """
        cache_read = sum(t.cache_read_tokens for t in self.per_model.values())
        prompt = sum(t.input_tokens for t in self.per_model.values()) + cache_read
        return cache_read / prompt if prompt else 0.0

    def summary(self) -> dict[str, Any]:
        """Compact dict for logs / observability emission."""
        return {
            "total_cost_usd": round(self.total_cost_usd(), 6),
            "total_tokens": self.total_tokens(),
            "cache_hit_rate": round(self.cache_hit_rate(), 4),
            "models": sorted(self.per_model.keys()),
            "tool_count": len(self.per_tool_duration_ms),
        }


def format_cost_summary(ledger: CostLedger) -> str:
    """Render a human-friendly per-model cost/token table for operators."""
    if not ledger.per_model:
        return "no model usage recorded"
    lines = [f"{'model':<30} {'in':>8} {'out':>8} {'cache':>8} {'usd':>9}"]
    for name in sorted(ledger.per_model):
        tally = ledger.per_model[name]
        lines.append(
            f"{name[:30]:<30} {tally.input_tokens:>8} {tally.output_tokens:>8} "
            f"{tally.cache_read_tokens:>8} {tally.cost_usd:>9.4f}"
        )
    lines.append(
        f"{'TOTAL (hit ' + format(ledger.cache_hit_rate(), '.0%') + ')':<30} "
        f"{'':>8} {'':>8} {'':>8} {ledger.total_cost_usd():>9.4f}"
    )
    return "\n".join(lines)


__all__ = [
    "CostLedger",
    "ModelTokenTally",
    "format_cost_summary",
    "Pricing",
    "estimate_cost_usd",
    "lookup_pricing",
    "register_pricing",
]
