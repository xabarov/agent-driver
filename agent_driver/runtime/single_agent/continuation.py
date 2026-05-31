"""Compatibility shim for continuation intent helpers."""

from agent_driver.runtime.single_agent.lifecycle.continuation import (
    ContinuationIntent,
    analyze_continuation_intent,
)

__all__ = ["ContinuationIntent", "analyze_continuation_intent"]
