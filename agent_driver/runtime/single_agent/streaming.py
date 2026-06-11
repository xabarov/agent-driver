"""Compatibility shim for LLM streaming helpers."""

# SHIM-REMOVE-BY: 2026-12-01 (re-export kept for importers; see refactoring-plan-2026-06-10)

from agent_driver.runtime.single_agent.llm_step.streaming import (
    LlmStreamIdleTimeout,
    _append_reasoning_details,
    complete_streaming_request,
    emit_reasoning_delta_events,
    emit_token_delta_events,
    is_stream_enabled,
)

__all__ = [
    "LlmStreamIdleTimeout",
    "_append_reasoning_details",
    "complete_streaming_request",
    "emit_reasoning_delta_events",
    "emit_token_delta_events",
    "is_stream_enabled",
]
