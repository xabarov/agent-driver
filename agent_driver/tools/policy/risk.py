"""Risk ordering helpers for tool policy checks."""

from agent_driver.contracts.tools import ToolManifest

_RISK_ORDER = {"low": 1, "medium": 2, "high": 3}


def is_risk_at_or_above(manifest: ToolManifest, threshold: str) -> bool:
    """Return True when manifest risk is >= threshold."""
    return _RISK_ORDER[manifest.risk.value] >= _RISK_ORDER[threshold]


__all__ = ["is_risk_at_or_above"]
